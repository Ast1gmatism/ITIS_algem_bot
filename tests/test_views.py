import pytest

from algem_bot.errors import DomainError
from algem_bot.views import admin_view, board_view
from tests.helpers import ADMIN, GREEN, Harness


async def test_public_zone_has_only_task_booking_buttons(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а", "б"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    view = board_view(await h.repo.snapshot(), h.clock(), h.service.policy)
    assert len(view.buttons) == 1
    assert all(button.action == "book" for button in view.buttons)
    assert "своб. 1/2" in view.buttons[0].text
    assert "Студент Б" in view.text


async def test_private_admin_zone_contains_identity_and_current_indicators(h: Harness) -> None:
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    state = await h.service.admin_snapshot(ADMIN)
    view = admin_view(state)
    assert "Студент Б" in view.text
    assert "2 вых." in view.text and "4/10" in view.text
    assert any(button.action == "confirm" for button in view.buttons)


async def test_unprivileged_user_cannot_obtain_admin_snapshot(h: Harness) -> None:
    with pytest.raises(DomainError, match="forbidden"):
        await h.service.admin_snapshot(GREEN)


async def test_student_names_are_escaped_for_future_html_transport(h: Harness) -> None:
    def rename(state):
        state.users["green"].fio = "Студент <А> & Б"

    await h.repo.transact(rename)
    pub = await h.service.add_tasks(ADMIN, {"3.1": ["а"]})
    await h.service.book(GREEN, "3.1", session_id=pub.session_id)
    view = board_view(await h.repo.snapshot(), h.clock(), h.service.policy)
    assert "&lt;А&gt; &amp; Б" in view.text
    assert "Студент <А>" not in view.text
