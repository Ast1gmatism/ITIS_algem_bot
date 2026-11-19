"""Offline executable example. No bot token, Google credentials or network calls."""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from algem_bot.adapters.memory_sheet import MemorySheet
from algem_bot.engine import statistics
from algem_bot.models import Role, SheetRow, User
from algem_bot.service import AlgemService
from algem_bot.sheets_sync import SyncCoordinator
from algem_bot.storage import JsonStateStore, Repository
from algem_bot.views import admin_view, board_view


@dataclass
class _Clock:
    value: datetime = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


async def run_demo() -> None:
    # All identities and Telegram IDs in this example are synthetic.
    users = [
        User(id="admin", fio="Ответственный А", role=Role.ADMIN, outputs=6, tg_id=1001),
        User(id="green", fio="Студент Б", outputs=2, tg_id=1002),
        User(id="yellow", fio="Студент В", outputs=5, tg_id=1003),
        User(id="unbound", fio="Студент Г", outputs=3),
        User(id="outside", fio="Студент Д", role=Role.OUTSIDE),
    ]
    sheet = MemorySheet([SheetRow.from_user(user) for user in users])
    clock = _Clock()
    with tempfile.TemporaryDirectory(prefix="algem-demo-") as directory:
        path = Path(directory) / "state.json"
        repo = await Repository.open(JsonStateStore(path))
        service = AlgemService(repo, admin_ids=frozenset({1001}), clock=clock)
        sync = SyncCoordinator(repo, sheet, clock=clock)
        await sync.sync_once()

        request = await service.request_registration(1004, "unbound", "Тестовый аккаунт Г")
        await service.approve_registration(1001, request.id)
        publication = await service.add_tasks(1001, {"3.1": ["а", "б"], "3.2": ["а"]})
        await service.book(1002, "3.1", session_id=publication.session_id)
        await service.book(1004, "3.2", session_id=publication.session_id)
        clock.advance(20)
        await service.book(1003, "3.1", session_id=publication.session_id)

        print("OFFLINE: без сети, токенов и реальных данных\n")
        print("=== ПУБЛИЧНАЯ ЗОНА БРОНИ ===")
        public = board_view(await repo.snapshot(), clock(), service.policy)
        print(public.text)
        print("Кнопки:", " | ".join(button.text for button in public.buttons))
        print("\n=== ЛИЧНАЯ ПАНЕЛЬ ОТВЕТСТВЕННОГО ===")
        print(admin_view(await service.admin_snapshot(1001)).text)

        # Вышел не бронировавший; его другая бронь автоматически освободится.
        await service.confirm(
            1001, "3.1", "а", session_id=publication.session_id, solver_id="yellow"
        )
        await sync.sync_once()
        # Учитываем человека без Telegram на теперь свободном подпункте.
        await service.confirm(
            1001, "3.1", "б", session_id=publication.session_id, solver_id="outside"
        )
        await service.confirm(1001, "3.2", "а", session_id=publication.session_id)

        # Локально ожидается 7, но ручной итог 8 уже учитывает выходы — не 9.
        await service.add_tasks(1001, {"3.3": ["а"]})
        await service.confirm(
            1001, "3.3", "а", session_id=publication.session_id, solver_id="yellow"
        )
        sheet.rows["yellow"].outputs = 8
        report = await sync.sync_once()
        state = await repo.snapshot()
        restored = await Repository.open(JsonStateStore(path))
        if state != await restored.snapshot() or state.users["yellow"].outputs != 8:
            raise RuntimeError("Offline scenario failed its persistence/source-of-truth check")
        stats = statistics(state.users.values())
        summary = {
            "outputs": {uid: user.outputs for uid, user in state.users.items()},
            "outside_tg_id": state.users["outside"].tg_id,
            "active_maximum": stats.maximum,
            "pending_fields": report.pending_fields,
            "restart_preserved_state": True,
        }
        print("\n=== ПОСЛЕ ПОДТВЕРЖДЕНИЯ И СИНХРОНИЗАЦИИ ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("Внесистемный учтён без Telegram; ручной итог — 8, не 9.")


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
