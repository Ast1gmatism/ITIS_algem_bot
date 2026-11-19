import pytest
from pydantic import ValidationError

from algem_bot.config import Policy, aware_time
from algem_bot.engine import is_admin, score
from algem_bot.errors import DomainError
from algem_bot.models import (
    BotState,
    PendingField,
    RegistrationStatus,
    Role,
    SheetRow,
    SyncState,
    User,
)
from algem_bot.views import admin_view, board_view
from tests.helpers import ADMIN, GREEN, START, Harness


def test_bootstrap_does_not_override_explicit_sheet_role() -> None:
    state = BotState(users={"a": User(id="a", fio="Студент А", tg_id=ADMIN)})
    assert not is_admin(state, ADMIN, frozenset({ADMIN}))
    assert is_admin(BotState(), ADMIN, frozenset({ADMIN}))


def test_bool_is_not_a_telegram_actor() -> None:
    assert not is_admin(BotState(), True, frozenset({1}))


def test_pending_target_must_match_current_local_field() -> None:
    with pytest.raises(ValidationError):
        BotState(
            users={"a": User(id="a", fio="Студент А", outputs=2)},
            sync=SyncState(
                change_seq=1,
                pending={"a": {"outputs": PendingField(base=1, target=99, revision=1)}},
            ),
        )


async def test_corrupted_derived_cells_do_not_become_source_data(h: Harness) -> None:
    row = SheetRow.model_validate(
        {**h.sheet.rows["green"].model_dump(), "score": 99, "category": "manual nonsense"}
    )
    h.sheet.rows["green"] = row
    await h.sync.sync_once()
    assert h.sheet.rows["green"].score == 4
    assert h.sheet.rows["green"].category == "priority"


async def test_duplicate_registration_request_does_not_create_new_work(h: Harness) -> None:
    def add(state):
        state.users["new"] = User(id="new", fio="Студент Е")

    await h.repo.transact(add)
    first = await h.service.request_registration(1005, "new", "Тестовый аккаунт")
    revision = (await h.repo.snapshot()).revision
    second = await h.service.request_registration(1005, "new", "Тестовый аккаунт")
    assert first.id == second.id
    assert (await h.repo.snapshot()).revision == revision


async def test_rejected_binding_never_grants_booking_access(h: Harness) -> None:
    def add(state):
        state.users["new"] = User(id="new", fio="Студент Е")

    await h.repo.transact(add)
    request = await h.service.request_registration(1005, "new", "Тестовый аккаунт")
    with pytest.raises(DomainError, match="forbidden"):
        await h.service.approve_registration(GREEN, request.id)
    await h.service.reject_registration(ADMIN, request.id)
    state = await h.repo.snapshot()
    assert state.users["new"].tg_id is None
    assert state.registrations[request.id].status == RegistrationStatus.REJECTED
    with pytest.raises(DomainError, match="registration_closed"):
        await h.service.approve_registration(ADMIN, request.id)


async def test_approved_binding_cannot_be_rejected_as_if_pending(h: Harness) -> None:
    def add(state):
        state.users["new"] = User(id="new", fio="Студент Е")

    await h.repo.transact(add)
    request = await h.service.request_registration(1005, "new", "Тестовый аккаунт")
    await h.service.approve_registration(ADMIN, request.id)
    await h.service.approve_registration(ADMIN, request.id)
    with pytest.raises(DomainError, match="registration_closed"):
        await h.service.reject_registration(ADMIN, request.id)


async def test_invalid_task_batch_is_not_partially_applied(h: Harness) -> None:
    before = await h.repo.snapshot()
    with pytest.raises(DomainError, match="invalid_task"):
        await h.service.add_tasks(ADMIN, {"3.1": ["а"], "not a task": ["б"]})
    assert await h.repo.snapshot() == before


async def test_missing_solver_and_already_completed_other_solver_are_rejected(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    with pytest.raises(DomainError, match="choose_solver"):
        await h.service.confirm(ADMIN, "3.1", "а", session_id=pub.session_id)
    with pytest.raises(DomainError, match="unknown_person"):
        await h.service.confirm(ADMIN, "3.1", "а", session_id=pub.session_id, solver_id="missing")
    await h.service.confirm(ADMIN, "3.1", "а", session_id=pub.session_id, solver_id="outside")
    with pytest.raises(DomainError, match="already_completed"):
        await h.service.confirm(ADMIN, "3.1", "а", session_id=pub.session_id, solver_id="green")
    assert not await h.service.release(ADMIN, "3.1", "а", session_id=pub.session_id)


async def test_pending_registration_is_visible_only_in_admin_view(h: Harness) -> None:
    def add(state):
        state.users["new"] = User(id="new", fio="Студент Е")

    await h.repo.transact(add)
    await h.service.request_registration(1005, "new", "Тестовый аккаунт")
    state = await h.repo.snapshot()
    assert "Тестовый аккаунт" not in board_view(state, h.clock(), Policy()).text
    private = admin_view(await h.service.admin_snapshot(ADMIN))
    assert "Тестовый аккаунт" in private.text
    assert any(button.action == "approve_registration" for button in private.buttons)


async def test_public_view_reports_automatic_red_access(h: Harness) -> None:
    await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    h.clock.advance(60)
    view = board_view(await h.repo.snapshot(), h.clock(), Policy())
    assert "🔴 доступ открыт" in view.text


@pytest.mark.parametrize(("outputs", "maximum"), [(-1, 5), (1, -5), (True, 5)])
def test_score_rejects_invalid_counts(outputs, maximum) -> None:
    with pytest.raises(ValueError):
        score(outputs, maximum)


def test_naive_clock_is_rejected() -> None:
    with pytest.raises(ValueError):
        aware_time(START.replace(tzinfo=None))


async def test_manual_role_revocation_is_not_overridden_by_bootstrap(h: Harness) -> None:
    h.sheet.rows["admin"].role = Role.STUDENT
    await h.sync.sync_once()
    with pytest.raises(DomainError, match="forbidden"):
        await h.service.clear(ADMIN)
