import asyncio

import pytest

from algem_bot.adapters.memory_sheet import MemorySheet
from algem_bot.errors import SyncError
from algem_bot.models import Role, SubtaskStatus
from algem_bot.sheets_sync import SyncCoordinator
from algem_bot.storage import JsonStateStore, Repository
from tests.helpers import ADMIN, GREEN, Harness


async def submit(h: Harness, label: str = "а") -> None:
    state = await h.repo.snapshot()
    await h.service.confirm(
        ADMIN, "3.1", label, session_id=state.session_id, solver_id="green"
    )


async def test_ac07_stale_pull_does_not_erase_unsent_increment(h: Harness) -> None:
    await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await submit(h)
    report = await h.sync.sync_once()
    assert h.sheet.rows["green"].outputs == 3
    assert (await h.repo.snapshot()).users["green"].outputs == 3
    assert report.pending_fields == 0


async def test_ac08_new_manual_total_wins_without_double_increment(h: Harness) -> None:
    await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await submit(h)
    h.sheet.rows["green"].outputs = 8
    await h.sync.sync_once()
    state = await h.repo.snapshot()
    assert state.users["green"].outputs == 8
    assert h.sheet.rows["green"].outputs == 8
    assert not state.sync.pending


async def test_read_failure_preserves_local_state_and_records_failure(h: Harness) -> None:
    await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await submit(h)
    h.sheet.fail_read = True
    with pytest.raises(SyncError):
        await h.sync.sync_once()
    state = await h.repo.snapshot()
    assert state.users["green"].outputs == 3 and state.sync.pending
    assert state.sync.last_error is not None
    h.sheet.fail_read = False
    await h.sync.sync_once()
    assert not (await h.repo.snapshot()).sync.pending


async def test_write_failure_survives_restart_and_retries(h: Harness) -> None:
    await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await submit(h)
    h.sheet.fail_write = True
    with pytest.raises(SyncError):
        await h.sync.sync_once()
    restored = await Repository.open(JsonStateStore(h.path))
    assert (await restored.snapshot()).sync.pending["green"]["outputs"].target == 3
    h.sheet.fail_write = False
    await SyncCoordinator(restored, h.sheet, clock=h.clock).sync_once()
    assert h.sheet.rows["green"].outputs == 3
    assert not (await restored.snapshot()).sync.pending


async def test_new_local_revision_during_write_is_not_cleared(h: Harness) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    class SlowWrite(MemorySheet):
        async def write_rows(self, patches):
            started.set()
            await release.wait()
            await super().write_rows(patches)
    sheet = SlowWrite(list(h.sheet.rows.values()))
    sync = SyncCoordinator(h.repo, sheet, clock=h.clock)
    await h.service.add_tasks(ADMIN, {"3.1": ["а", "б"]})
    await submit(h, "а")
    operation = asyncio.create_task(sync.sync_once())
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.wait_for(submit(h, "б"), 2)
    release.set()
    await operation
    pending = (await h.repo.snapshot()).sync.pending["green"]["outputs"]
    assert pending.base == 3 and pending.target == 4
    await sync.sync_once()
    assert sheet.rows["green"].outputs == 4
    assert not (await h.repo.snapshot()).sync.pending


async def test_uncertain_write_and_new_revision_are_recognized_after_restart(h: Harness) -> None:
    written, release = asyncio.Event(), asyncio.Event()
    class CommitThenFail(MemorySheet):
        fail_once = True
        async def write_rows(self, patches):
            await super().write_rows(patches)
            if self.fail_once:
                self.fail_once = False
                written.set()
                await release.wait()
                raise OSError("synthetic lost acknowledgement")
    sheet = CommitThenFail(list(h.sheet.rows.values()))
    await h.service.add_tasks(ADMIN, {"3.1": ["а", "б"]})
    await submit(h, "а")
    sync = SyncCoordinator(h.repo, sheet, clock=h.clock)
    operation = asyncio.create_task(sync.sync_once())
    await asyncio.wait_for(written.wait(), 2)
    await submit(h, "б")
    release.set()
    with pytest.raises(SyncError):
        await operation
    restored = await Repository.open(JsonStateStore(h.path))
    await SyncCoordinator(restored, sheet, clock=h.clock).sync_once()
    assert sheet.rows["green"].outputs == 4
    assert (await restored.snapshot()).users["green"].outputs == 4
    assert not (await restored.snapshot()).sync.pending


async def test_network_read_does_not_hold_state_lock(h: Harness) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    class SlowRead(MemorySheet):
        async def read_rows(self):
            started.set()
            await release.wait()
            return await super().read_rows()
    sheet = SlowRead(list(h.sheet.rows.values()))
    await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    state = await h.repo.snapshot()
    operation = asyncio.create_task(SyncCoordinator(h.repo, sheet, clock=h.clock).sync_once())
    await asyncio.wait_for(started.wait(), 2)
    result = await asyncio.wait_for(
        h.service.book(GREEN, "3.1", session_id=state.session_id), 2
    )
    assert result.action == "reserved"
    release.set()
    await operation


async def test_missing_row_does_not_delete_person_or_erase_pending(h: Harness) -> None:
    await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await submit(h)
    del h.sheet.rows["green"]
    report = await h.sync.sync_once()
    state = await h.repo.snapshot()
    assert state.users["green"].outputs == 3
    assert state.sync.pending["green"]["outputs"].target == 3
    assert report.missing_user_ids == ("green",)
    assert report.pending_fields == 1
    assert "green" not in h.sheet.rows


async def test_external_role_change_to_outside_releases_booking(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    h.sheet.rows["green"].role = Role.OUTSIDE
    h.sheet.rows["green"].tg_id = None
    await h.sync.sync_once()
    state = await h.repo.snapshot()
    assert state.tasks["3.1"].subtasks["а"].status == SubtaskStatus.FREE
    assert state.users["green"].role == Role.OUTSIDE


async def test_derivatives_are_written_but_not_trusted_as_inputs(h: Harness) -> None:
    h.sheet.rows["green"].score = 10
    await h.sync.sync_once()
    assert h.sheet.rows["green"].score == 4
    assert h.sheet.rows["green"].category.value == "priority"


async def test_duplicate_external_telegram_ids_do_not_partially_apply(h: Harness) -> None:
    h.sheet.rows["green"].tg_id = h.sheet.rows["yellow"].tg_id
    before = await h.repo.snapshot()
    with pytest.raises(SyncError):
        await h.sync.sync_once()
    after = await h.repo.snapshot()
    assert after.users == before.users


async def test_fresh_repository_imports_people_without_tg_ids(tmp_path, h: Harness) -> None:
    repo = await Repository.open(JsonStateStore(tmp_path / "import.json"))
    await SyncCoordinator(repo, h.sheet, clock=h.clock).sync_once()
    state = await repo.snapshot()
    assert state.users["outside"].tg_id is None
    assert len(state.users) == len(h.sheet.rows)
