from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import escape

from algem_bot.config import Policy
from algem_bot.engine import booking_gate, category, green_window_active, score, statistics
from algem_bot.models import BotState, Category, RegistrationStatus, SubtaskStatus


@dataclass(frozen=True)
class Button:
    text: str
    action: str
    session_id: int
    task_number: str = ""
    label: str = ""
    request_id: str = ""


@dataclass(frozen=True)
class View:
    """Transport-independent HTML view; network limits are handled in M2."""

    text: str
    buttons: tuple[Button, ...]


def _natural_number(value: str) -> str:
    return re.sub(r"\d+", lambda match: match.group().zfill(24), value)


def board_view(state: BotState, now: datetime, policy: Policy) -> View:
    stats = statistics(state.users.values())
    has_free = any(
        sub.status == SubtaskStatus.FREE
        for task in state.tasks.values()
        for sub in task.subtasks.values()
    )
    if not has_free:
        phase = "Нет свободных подпунктов"
    elif green_window_active(state, now, policy):
        phase = f"Первые {policy.green_window_seconds} секунд — 🟢"
    else:
        phase = "Окно 🟡 открыто"
    leader = next(
        (user for user in state.users.values() if category(user, stats) == Category.BENCHMARK),
        None,
    )
    red_status = ""
    if leader is not None and has_free:
        gate = booking_gate(state, leader, now, policy)
        red_status = (
            "🔴 доступ открыт" if gate.allowed else f"🔴 ожидание: {gate.wait_seconds} сек."
        )
    lines = [
        "<b>Алгем · запись на номера</b>",
        f"Средний темп: {stats.mean:.2f} / {stats.maximum}",
        phase,
    ]
    if red_status:
        lines.append(red_status)
    buttons: list[Button] = []
    for number in sorted(state.tasks, key=_natural_number):
        task = state.tasks[number]
        free = sum(sub.status == SubtaskStatus.FREE for sub in task.subtasks.values())
        lines.append(f"\n<b>{escape(number)}</b>")
        for label, subtask in sorted(task.subtasks.items()):
            prefix = f"{escape(label)} — "
            if subtask.status == SubtaskStatus.FREE:
                lines.append(prefix + "свободно")
            elif subtask.reserved_by is not None:
                lines.append(prefix + escape(state.users[subtask.reserved_by].fio))
            else:
                assert subtask.solved_by is not None
                lines.append(prefix + "✅ " + escape(state.users[subtask.solved_by].fio))
        lock = "🔒 " if not free else ""
        buttons.append(
            Button(
                text=f"{lock}{number} · своб. {free}/{len(task.subtasks)}",
                action="book",
                session_id=state.session_id,
                task_number=number,
            )
        )
    if not state.tasks:
        lines.append("Пока нет задач. Добавление: /add 3.1 а б")
    return View("\n".join(lines), tuple(buttons))


def admin_view(state: BotState) -> View:
    """Render only an already authorized snapshot from AlgemService.admin_snapshot."""
    stats = statistics(state.users.values())
    lines = ["<b>Алгем · панель ответственного</b>"]
    if state.sync.last_error:
        lines.append("⚠ " + escape(state.sync.last_error))
    pending_count = sum(len(fields) for fields in state.sync.pending.values())
    if pending_count:
        lines.append(f"Ожидают синхронизации: {pending_count} полей")
    buttons: list[Button] = []
    for number in sorted(state.tasks, key=_natural_number):
        for label, subtask in sorted(state.tasks[number].subtasks.items()):
            title = f"{number} {label}"
            uid = subtask.reserved_by or subtask.solved_by
            if uid is not None:
                user = state.users[uid]
                color = category(user, stats).icon
                lines.append(
                    f"{escape(title)} — {color} {escape(user.fio)} · "
                    f"{user.outputs} вых. · {score(user.outputs, stats.maximum)}/10"
                )
            else:
                lines.append(f"{escape(title)} — свободно")
            if subtask.status == SubtaskStatus.DONE:
                continue
            if subtask.reserved_by is not None:
                name = state.users[subtask.reserved_by].fio
                buttons.append(
                    Button(
                        text=f"✅ Сдал {name}",
                        action="confirm",
                        session_id=state.session_id,
                        task_number=number,
                        label=label,
                    )
                )
                buttons.append(
                    Button(
                        text=f"🗑 Снять {title}",
                        action="release",
                        session_id=state.session_id,
                        task_number=number,
                        label=label,
                    )
                )
            buttons.append(
                Button(
                    text=f"🔄 Сдал другой · {title}",
                    action="choose_solver",
                    session_id=state.session_id,
                    task_number=number,
                    label=label,
                )
            )
    for request in state.registrations.values():
        if request.status != RegistrationStatus.PENDING:
            continue
        user = state.users[request.user_id]
        lines.append(
            f"Привязка: {escape(request.display_name)} (TG {request.tg_id}) → {escape(user.fio)}"
        )
        for action, title in (
            ("approve_registration", "Одобрить"),
            ("reject_registration", "Отклонить"),
        ):
            buttons.append(
                Button(
                    text=f"{title}: {user.fio}",
                    action=action,
                    session_id=state.session_id,
                    request_id=request.id,
                )
            )
    return View("\n".join(lines), tuple(buttons))
