import asyncio

import pytest

from algem_bot.errors import DomainError
from algem_bot.models import Role, SubtaskStatus, User
from tests.helpers import ADMIN, GREEN, RED, YELLOW, Harness, build_harness, people


async def test_ac01_first_free_letter_without_increment(h: Harness) -> None:
    publication = await h.service.add_tasks(ADMIN, {"3.1": ["в", "а", "б"]})
    result = await h.service.book(GREEN, "3.1", session_id=publication.session_id)
    state = await h.repo.snapshot()
    assert result.label == "а" and result.action == "reserved"
    assert state.users["green"].outputs == 2
    assert state.tasks["3.1"].subtasks["а"].reserved_by == "green"


async def test_ac19_window_blocks_yellow_and_red(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а", "б"]})
    for tg_id in (YELLOW, RED):
        with pytest.raises(DomainError, match="green_window"):
            await h.service.book(tg_id, "3.1", session_id=pub.session_id)
    h.clock.advance(20)
    await h.service.book(YELLOW, "3.1", session_id=pub.session_id)


async def test_ac20_window_skips_when_all_green_already_booked(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а", "б"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    await h.service.book(YELLOW, "3.1", session_id=pub.session_id)


async def test_window_skips_unregistered_green_but_they_remain_in_average(h: Harness) -> None:
    def unlink(state):
        state.users["green"].tg_id = None
    await h.repo.transact(unlink)
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.book(YELLOW, "3.1", session_id=pub.session_id)


async def test_red_opens_at_sixty_seconds_and_booking_resets_timer(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а", "б"]})
    h.clock.advance(59)
    with pytest.raises(DomainError, match="benchmark_locked"):
        await h.service.book(RED, "3.1", session_id=pub.session_id)
    h.clock.advance(1)
    await h.service.book(RED, "3.1", session_id=pub.session_id)
    with pytest.raises(DomainError, match="benchmark_locked"):
        await h.service.book(ADMIN, "3.1", session_id=pub.session_id)
    result = await h.service.book(RED, "3.1", session_id=pub.session_id)
    assert result.action == "cancelled"
    with pytest.raises(DomainError, match="benchmark_locked"):
        await h.service.book(RED, "3.1", session_id=pub.session_id)


async def test_planned_red_still_waits_for_green_window(tmp_path) -> None:
    users = people()
    for uid, count in {"admin": 4, "green": 2, "yellow": 4, "red": 4}.items():
        users[uid].outputs = count
    h = await build_harness(tmp_path / "state.json", users)
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    with pytest.raises(DomainError, match="green_window"):
        await h.service.book(RED, "3.1", session_id=pub.session_id)
    h.clock.advance(20)
    await h.service.book(RED, "3.1", session_id=pub.session_id)


async def test_ac12_one_reservation_per_person(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"], "3.2": ["а"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    with pytest.raises(DomainError, match="already_reserved"):
        await h.service.book(GREEN, "3.2", session_id=pub.session_id)


async def test_ac13_cancel_own_reservation_does_not_change_counts(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    before = await h.repo.snapshot()
    result = await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    after = await h.repo.snapshot()
    assert result.action == "cancelled"
    assert after.users == before.users
    assert after.last_booking_at == before.last_booking_at


async def test_ac06_concurrent_clicks_only_one_wins(tmp_path) -> None:
    users = people()
    users["yellow"].outputs = 2
    h = await build_harness(tmp_path / "state.json", users)
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    results = await asyncio.gather(
        h.service.book(GREEN, "3.1", session_id=pub.session_id),
        h.service.book(YELLOW, "3.1", session_id=pub.session_id),
        return_exceptions=True,
    )
    assert sum(isinstance(result, DomainError) for result in results) == 1
    error = next(result for result in results if isinstance(result, DomainError))
    assert error.code == "task_full"


async def test_ac04_actual_solver_gets_credit_and_other_booking_is_released(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"], "3.2": ["б"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    await h.service.book(YELLOW, "3.2", session_id=pub.session_id)
    result = await h.service.confirm(
        ADMIN, "3.1", "а", session_id=pub.session_id, solver_id="yellow"
    )
    state = await h.repo.snapshot()
    assert result.applied and result.solver_id == "yellow"
    assert state.users["green"].outputs == 2
    assert state.users["yellow"].outputs == 6
    assert state.tasks["3.1"].subtasks["а"].solved_by == "yellow"
    assert state.tasks["3.2"].subtasks["б"].status == SubtaskStatus.FREE


async def test_ac02_default_solver_is_known_reserver(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    await h.service.confirm(ADMIN, "3.1", "а", session_id=pub.session_id)
    assert (await h.repo.snapshot()).users["green"].outputs == 3


async def test_ac14_double_confirmation_is_idempotent(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    first = await h.service.confirm(
        ADMIN, "3.1", "а", session_id=pub.session_id, solver_id="green"
    )
    second = await h.service.confirm(
        ADMIN, "3.1", "а", session_id=pub.session_id, solver_id="green"
    )
    assert first.applied and not second.applied
    assert (await h.repo.snapshot()).users["green"].outputs == 3


async def test_ac21_outside_without_telegram_can_submit(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.confirm(
        ADMIN, "3.1", "а", session_id=pub.session_id, solver_id="outside"
    )
    state = await h.repo.snapshot()
    assert state.users["outside"].outputs == 1
    assert state.users["outside"].tg_id is None
    assert "outputs" in state.sync.pending["outside"]


async def test_ac05_merge_preserves_reservations_and_completions(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а", "б"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    await h.service.confirm(
        ADMIN, "3.1", "б", session_id=pub.session_id, solver_id="outside"
    )
    await h.service.add_tasks(GREEN, {"3.1": ["в", "г", "а"]})
    subs = (await h.repo.snapshot()).tasks["3.1"].subtasks
    assert set(subs) == {"а", "б", "в", "г"}
    assert subs["а"].reserved_by == "green"
    assert subs["б"].solved_by == "outside"


async def test_ac23_duplicate_add_does_not_ping_or_restart_window(h: Harness) -> None:
    first = await h.service.add_tasks(ADMIN, {"3.1": ["а"], "3.2": ["б"]})
    before = await h.repo.snapshot()
    h.clock.advance(10)
    second = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    after = await h.repo.snapshot()
    assert first.announcement is not None
    assert first.announcement.priority_tg_ids == (GREEN,)
    assert second.added == 0 and second.announcement is None
    assert after.published_at == before.published_at
    assert after.revision == before.revision


async def test_ac09_clear_preserves_counts_and_unsent_changes(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.confirm(
        ADMIN, "3.1", "а", session_id=pub.session_id, solver_id="green"
    )
    before = await h.repo.snapshot()
    new_session = await h.service.clear(ADMIN)
    after = await h.repo.snapshot()
    assert new_session != pub.session_id and not after.tasks
    assert after.users == before.users and after.sync.pending == before.sync.pending


async def test_ac18_stale_callbacks_cannot_change_new_session(h: Harness) -> None:
    old = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.clear(ADMIN)
    await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    with pytest.raises(DomainError, match="stale_session"):
        await h.service.book(GREEN, "3.1", session_id=old.session_id)
    with pytest.raises(DomainError, match="stale_session"):
        await h.service.confirm(
            ADMIN, "3.1", "а", session_id=old.session_id, solver_id="outside"
        )


async def test_ac17_non_admin_cannot_confirm_or_clear(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    with pytest.raises(DomainError, match="forbidden"):
        await h.service.confirm(
            GREEN, "3.1", "а", session_id=pub.session_id, solver_id="green"
        )
    with pytest.raises(DomainError, match="forbidden"):
        await h.service.clear(GREEN)


async def test_ac10_registration_requires_approval(h: Harness) -> None:
    def add_unbound(state):
        user = User(id="new", fio="Студент Е", outputs=1)
        state.users[user.id] = user
    await h.repo.transact(add_unbound)
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    request = await h.service.request_registration(1005, "new", "Тестовый аккаунт")
    with pytest.raises(DomainError, match="registration_pending"):
        await h.service.book(1005, "3.1", session_id=pub.session_id)
    await h.service.approve_registration(ADMIN, request.id)
    await h.service.book(1005, "3.1", session_id=pub.session_id)
    state = await h.repo.snapshot()
    assert state.users["new"].tg_id == 1005
    assert state.sync.pending["new"]["tg_id"].target == 1005


async def test_ac22_two_claims_one_name_only_one_approval(h: Harness) -> None:
    def add_unbound(state):
        state.users["new"] = User(id="new", fio="Студент Е")
    await h.repo.transact(add_unbound)
    first = await h.service.request_registration(1005, "new", "Аккаунт А")
    second = await h.service.request_registration(1006, "new", "Аккаунт Б")
    results = await asyncio.gather(
        h.service.approve_registration(ADMIN, first.id),
        h.service.approve_registration(ADMIN, second.id),
        return_exceptions=True,
    )
    assert sum(isinstance(result, DomainError) for result in results) == 1
    assert (await h.repo.snapshot()).users["new"].tg_id in {1005, 1006}


async def test_outside_cannot_request_binding(h: Harness) -> None:
    with pytest.raises(DomainError, match="outside"):
        await h.service.request_registration(1005, "outside", "Тестовый аккаунт")


async def test_ac10_unknown_click_has_registration_hint(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    with pytest.raises(DomainError, match="not_registered"):
        await h.service.book(9999, "3.1", session_id=pub.session_id)


async def test_admin_release_is_not_a_submission(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    await h.service.release(ADMIN, "3.1", "а", session_id=pub.session_id)
    state = await h.repo.snapshot()
    assert state.tasks["3.1"].subtasks["а"].status == SubtaskStatus.FREE
    assert state.users["green"].outputs == 2


async def test_bootstrap_admin_can_account_when_everyone_is_outside(tmp_path) -> None:
    users = {"x": User(id="x", fio="Студент А", role=Role.OUTSIDE)}
    h = await build_harness(tmp_path / "state.json", users)
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.confirm(ADMIN, "3.1", "а", session_id=pub.session_id, solver_id="x")
    assert (await h.repo.snapshot()).users["x"].outputs == 1
