from __future__ import annotations

import asyncio
import copy
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import ValidationError

from algem_bot.errors import StorageError
from algem_bot.models import BotState

T = TypeVar("T")


class StateStore(Protocol):
    async def load(self) -> BotState: ...

    async def save(self, state: BotState) -> None: ...


class JsonStateStore:
    """Atomic local persistence for one process; not a distributed database."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def load(self) -> BotState:
        return await asyncio.to_thread(self._load)

    def _load(self) -> BotState:
        try:
            return BotState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return BotState()
        except (OSError, UnicodeError, ValidationError) as exc:
            raise StorageError(
                "Cannot read or validate state; existing file was not reset"
            ) from exc

    async def save(self, state: BotState) -> None:
        payload = BotState.model_validate(state.model_dump()).model_dump_json(indent=2)
        await asyncio.to_thread(self._save_text, payload)

    def _save_text(self, payload: str) -> None:
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(
                dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp"
            )
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            # Best effort: some filesystems/platforms do not support directory fsync.
            with suppress(OSError):
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except OSError as exc:
            raise StorageError("Cannot atomically save state") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)


class MemoryStateStore:
    def __init__(self, state: BotState | None = None) -> None:
        self._saved = (state or BotState()).model_copy(deep=True)

    async def load(self) -> BotState:
        return self._saved.model_copy(deep=True)

    async def save(self, state: BotState) -> None:
        self._saved = state.model_copy(deep=True)


class Repository:
    def __init__(self, state: BotState, store: StateStore) -> None:
        self._state = BotState.model_validate(state.model_dump())
        self._store = store
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, store: StateStore) -> Repository:
        return cls(await store.load(), store)

    async def snapshot(self) -> BotState:
        async with self._lock:
            return self._state.model_copy(deep=True)

    async def transact(self, operation: Callable[[BotState], T]) -> T:
        async with self._lock:
            candidate = self._state.model_copy(deep=True)
            result = operation(candidate)
            validated = BotState.model_validate(candidate.model_dump())
            if validated == self._state:
                return copy.deepcopy(result)
            validated.revision = self._state.revision + 1
            saving = asyncio.create_task(self._store.save(validated))
            cancelled = False
            while True:
                try:
                    await asyncio.shield(saving)
                    break
                except asyncio.CancelledError:
                    if saving.cancelled():
                        raise
                    cancelled = True
            self._state = validated
            if cancelled:
                raise asyncio.CancelledError
            return copy.deepcopy(result)
