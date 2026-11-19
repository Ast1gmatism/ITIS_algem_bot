import asyncio
import os

import pytest
from pydantic import ValidationError

from algem_bot.errors import StorageError
from algem_bot.models import BotState, Subtask, SubtaskStatus, Task, User
from algem_bot.storage import JsonStateStore, MemoryStateStore, Repository
from tests.helpers import Harness, state_for


async def test_json_restart_preserves_reservation_pending_registration_and_outbox(
    h: Harness,
) -> None:
    def add_unbound(state: BotState) -> None:
        state.users["pending"] = User(id="pending", fio="Студент Е")

    await h.repo.transact(add_unbound)
    request = await h.service.request_registration(1005, "pending", "Тестовый аккаунт Е")
    pub = await h.service.add_tasks(1001, {"3.1": ["а", "б"]})
    await h.service.book(1002, "3.1", session_id=pub.session_id)
    await h.service.confirm(1001, "3.1", "б", session_id=pub.session_id, solver_id="outside")
    restored = await Repository.open(JsonStateStore(h.path))
    loaded = await restored.snapshot()
    assert loaded == await h.repo.snapshot()
    assert loaded.sync.pending["outside"]["outputs"].target == 1
    assert loaded.registrations[request.id].status == "pending"
    assert loaded.users["pending"].tg_id is None


async def test_corrupt_state_is_not_silently_reset(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"broken":', encoding="utf-8")
    with pytest.raises(StorageError):
        await Repository.open(JsonStateStore(path))
    assert path.read_text(encoding="utf-8") == '{"broken":'


async def test_missing_state_starts_empty_without_erasing_anything(tmp_path) -> None:
    repo = await Repository.open(JsonStateStore(tmp_path / "state.json"))
    assert (await repo.snapshot()).users == {}


async def test_atomic_replace_failure_preserves_previous_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state.json"
    store = JsonStateStore(path)
    state = state_for()
    await store.save(state)
    original = path.read_bytes()

    def fail_replace(*args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    state.users["green"].outputs += 1
    with pytest.raises(StorageError):
        await store.save(state)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


async def test_failed_repository_save_does_not_publish_memory_change() -> None:
    class FailingStore(MemoryStateStore):
        async def save(self, state):
            raise StorageError("synthetic disk failure")

    repo = await Repository.open(FailingStore(state_for()))

    def mutate(state):
        state.users["green"].outputs += 1

    with pytest.raises(StorageError):
        await repo.transact(mutate)
    assert (await repo.snapshot()).users["green"].outputs == 2


async def test_mutator_exception_rolls_back_every_field(h: Harness) -> None:
    before = await h.repo.snapshot()

    def mutate(state):
        state.users["green"].outputs = 20
        raise ValueError("synthetic mutation failure")

    with pytest.raises(ValueError):
        await h.repo.transact(mutate)
    assert await h.repo.snapshot() == before


async def test_cancelled_caller_cannot_separate_disk_and_memory() -> None:
    started, release = asyncio.Event(), asyncio.Event()

    class BlockingStore(MemoryStateStore):
        async def save(self, state):
            started.set()
            await release.wait()
            await super().save(state)

    store = BlockingStore(state_for())
    repo = await Repository.open(store)

    def mutate(state):
        state.users["green"].outputs += 1

    operation = asyncio.create_task(repo.transact(mutate))
    await asyncio.wait_for(started.wait(), timeout=2)
    operation.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert await repo.snapshot() == await store.load()
    assert (await repo.snapshot()).users["green"].outputs == 3


async def test_snapshot_is_detached(h: Harness) -> None:
    snapshot = await h.repo.snapshot()
    snapshot.users["green"].outputs = 999
    assert (await h.repo.snapshot()).users["green"].outputs == 2


def test_duplicate_telegram_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        BotState(
            users={
                "a": User(id="a", fio="Студент А", tg_id=1001),
                "b": User(id="b", fio="Студент Б", tg_id=1001),
            }
        )


@pytest.mark.parametrize("value", [-1, 1.5, True, "3"])
def test_counts_are_strict_nonnegative_integers(value) -> None:
    with pytest.raises(ValidationError):
        User(id="a", fio="Студент А", outputs=value)


def test_one_person_cannot_have_two_reservations_in_json() -> None:
    user = User(id="a", fio="Студент А", tg_id=1001)
    with pytest.raises(ValidationError):
        BotState(
            users={"a": user},
            tasks={
                "3.1": Task(
                    number="3.1",
                    subtasks={
                        "а": Subtask(status=SubtaskStatus.RESERVED, reserved_by="a"),
                        "б": Subtask(status=SubtaskStatus.RESERVED, reserved_by="a"),
                    },
                )
            },
        )


def test_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BotState(schema_version=999)
