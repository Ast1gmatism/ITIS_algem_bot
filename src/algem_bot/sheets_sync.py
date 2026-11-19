from __future__ import annotations

import asyncio
from dataclasses import dataclass

from algem_bot.config import Clock, aware_time, utc_now
from algem_bot.engine import category, score, statistics
from algem_bot.errors import SyncError
from algem_bot.models import (
    SOURCE_FIELDS,
    BotState,
    Role,
    SheetRow,
    Subtask,
    WriteAttempt,
    source_value,
)
from algem_bot.ports import SheetPatch, SheetPort
from algem_bot.storage import Repository


@dataclass(frozen=True)
class SyncReport:
    imported_rows: int
    written_rows: int
    pending_fields: int
    missing_user_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Plan:
    imported_rows: int
    patches: list[SheetPatch]


class SyncCoordinator:
    """Revision-aware reconciliation; no network I/O under the repository lock.

    External totals win when they differ from the known base and known own writes.
    A normal Sheets API adapter cannot promise CAS against a browser editor.
    """

    def __init__(self, repo: Repository, port: SheetPort, *, clock: Clock = utc_now) -> None:
        self.repo = repo
        self.port = port
        self.clock = clock
        self._sync_lock = asyncio.Lock()

    @staticmethod
    def _merge_and_plan(state: BotState, rows: list[SheetRow]) -> _Plan:
        incoming = {row.id: row for row in rows}
        if len(incoming) != len(rows):
            raise ValueError("Duplicate logical row IDs")
        imported = 0
        for uid, row in incoming.items():
            if uid not in state.users:
                state.users[uid] = row.as_user()
                imported += 1
            user = state.users[uid]
            pending_fields = state.sync.pending.get(uid, {})
            for field in SOURCE_FIELDS:
                remote = source_value(row, field)
                pending = pending_fields.get(field)
                if pending is None:
                    setattr(user, field, getattr(row, field))
                    continue
                if remote == pending.target:
                    # The desired value is already present, including an uncertain previous write.
                    setattr(user, field, getattr(row, field))
                    del pending_fields[field]
                    continue
                if remote == pending.base:
                    # A stale pull is not a manual edit.
                    continue
                recognized = [attempt for attempt in pending.attempts if attempt.target == remote]
                if recognized:
                    own = max(recognized, key=lambda attempt: attempt.revision)
                    pending.base = remote
                    pending.attempts = [
                        attempt for attempt in pending.attempts if attempt.revision > own.revision
                    ]
                    continue
                # A new external total is authoritative and already includes the local work.
                setattr(user, field, getattr(row, field))
                del pending_fields[field]
            if not pending_fields:
                state.sync.pending.pop(uid, None)
            state.sync.snapshots[uid] = row.model_copy(deep=True)

        state.sync.missing_user_ids = sorted(set(state.users) - set(incoming))
        for task in state.tasks.values():
            for label, subtask in list(task.subtasks.items()):
                if subtask.reserved_by is None:
                    continue
                user = state.users[subtask.reserved_by]
                if user.role == Role.OUTSIDE or user.tg_id is None:
                    task.subtasks[label] = Subtask()

        stats = statistics(state.users.values())
        patches: list[SheetPatch] = []
        for uid, row in incoming.items():
            pending_fields = state.sync.pending.get(uid, {})
            fields = {field: pending.target for field, pending in pending_fields.items()}
            revisions = {field: pending.revision for field, pending in pending_fields.items()}
            user = state.users[uid]
            desired_score = score(user.outputs, stats.maximum)
            desired_category = category(user, stats)
            derived_changed = row.score != desired_score or row.category != desired_category
            if not fields and not derived_changed:
                continue
            for pending in pending_fields.values():
                if not any(
                    attempt.revision == pending.revision and attempt.target == pending.target
                    for attempt in pending.attempts
                ):
                    # Persisted by Repository BEFORE external I/O, including ambiguous failures.
                    pending.attempts.append(
                        WriteAttempt(revision=pending.revision, target=pending.target)
                    )
            patches.append(
                SheetPatch(
                    user_id=uid,
                    fields=fields,
                    revisions=revisions,
                    score=desired_score if row.score != desired_score else None,
                    category=desired_category if row.category != desired_category else None,
                )
            )
        return _Plan(imported, patches)

    def _acknowledge(self, state: BotState, patches: list[SheetPatch]) -> None:
        for patch in patches:
            uid = patch.user_id
            previous = state.sync.snapshots[uid]
            payload = previous.model_dump()
            for key, value in patch.fields.items():
                payload[key] = value
            if patch.score is not None:
                payload["score"] = patch.score
            if patch.category is not None:
                payload["category"] = patch.category
            state.sync.snapshots[uid] = SheetRow.model_validate(payload)
            pending_fields = state.sync.pending.get(uid, {})
            for field, target in patch.fields.items():
                pending = pending_fields.get(field)
                if pending is None:
                    continue
                sent_revision = patch.revisions[field]
                if pending.revision == sent_revision or pending.target == target:
                    del pending_fields[field]
                else:
                    # A newer local confirmation must remain pending against the new base.
                    pending.base = target
                    pending.attempts = [
                        attempt for attempt in pending.attempts if attempt.revision > sent_revision
                    ]
            if not pending_fields:
                state.sync.pending.pop(uid, None)
        state.sync.last_success_at = aware_time(self.clock())
        state.sync.last_error = (
            "Некоторые строки отсутствуют в ведомости; локальные данные сохранены"
            if state.sync.missing_user_ids
            else None
        )

    async def sync_once(self) -> SyncReport:
        async with self._sync_lock:
            try:
                raw_rows = await self.port.read_rows()
                rows = [SheetRow.model_validate(row.model_dump()) for row in raw_rows]
                plan = await self.repo.transact(lambda state: self._merge_and_plan(state, rows))
                if plan.patches:
                    await self.port.write_rows(plan.patches)
                await self.repo.transact(lambda state: self._acknowledge(state, plan.patches))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Exception text from a provider may contain secrets: persist only its type.
                message = f"Синхронизация не завершена ({type(exc).__name__})"

                def record_error(state: BotState) -> None:
                    state.sync.last_error = message

                await self.repo.transact(record_error)
                raise SyncError(message) from exc
            state = await self.repo.snapshot()
            return SyncReport(
                imported_rows=plan.imported_rows,
                written_rows=len(plan.patches),
                pending_fields=sum(len(fields) for fields in state.sync.pending.values()),
                missing_user_ids=tuple(state.sync.missing_user_ids),
            )
