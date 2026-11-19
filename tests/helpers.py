from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from algem_bot.adapters.memory_sheet import MemorySheet
from algem_bot.config import Policy
from algem_bot.models import BotState, Role, SheetRow, SyncState, User
from algem_bot.service import AlgemService
from algem_bot.sheets_sync import SyncCoordinator
from algem_bot.storage import JsonStateStore, Repository

START = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
ADMIN = 1001
GREEN = 1002
YELLOW = 1003
RED = 1004


@dataclass
class ManualClock:
    value: datetime = START

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def people() -> dict[str, User]:
    return {
        "admin": User(id="admin", fio="Ответственный А", role=Role.ADMIN, outputs=6, tg_id=ADMIN),
        "green": User(id="green", fio="Студент Б", outputs=2, tg_id=GREEN),
        "yellow": User(id="yellow", fio="Студент В", outputs=5, tg_id=YELLOW),
        "red": User(id="red", fio="Студент Г", outputs=6, tg_id=RED),
        "outside": User(id="outside", fio="Студент Д", role=Role.OUTSIDE, outputs=0),
    }


def state_for(users: dict[str, User] | None = None) -> BotState:
    users = people() if users is None else users
    rows = {key: SheetRow.from_user(user) for key, user in users.items()}
    return BotState(users=users, sync=SyncState(snapshots=rows))


@dataclass
class Harness:
    service: AlgemService
    repo: Repository
    sheet: MemorySheet
    sync: SyncCoordinator
    clock: ManualClock
    path: Path


async def build_harness(path: Path, users: dict[str, User] | None = None) -> Harness:
    clock = ManualClock()
    state = state_for(users)
    store = JsonStateStore(path)
    await store.save(state)
    repo = await Repository.open(store)
    service = AlgemService(repo, policy=Policy(), admin_ids=frozenset({ADMIN}), clock=clock)
    sheet = MemorySheet(list(state.sync.snapshots.values()))
    sync = SyncCoordinator(repo, sheet, clock=clock)
    return Harness(service, repo, sheet, sync, clock, path)
