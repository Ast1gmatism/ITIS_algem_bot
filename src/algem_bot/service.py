from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError

from algem_bot.config import Clock, Policy, aware_time, utc_now
from algem_bot.engine import (
    booking_gate,
    eligible_priority_ids,
    is_admin,
    reservation_for,
)
from algem_bot.errors import DomainError
from algem_bot.models import (
    BotState,
    RegistrationRequest,
    RegistrationStatus,
    Role,
    Subtask,
    SubtaskStatus,
    Task,
    User,
    mark_pending,
)
from algem_bot.storage import Repository


@dataclass(frozen=True)
class Announcement:
    batch_id: int
    priority_tg_ids: tuple[int, ...]


@dataclass(frozen=True)
class Publication:
    session_id: int
    added: int
    announcement: Announcement | None


@dataclass(frozen=True)
class BookingResult:
    action: str
    task_number: str
    label: str


@dataclass(frozen=True)
class ConfirmationResult:
    applied: bool
    solver_id: str


class AlgemService:
    def __init__(
        self,
        repo: Repository,
        *,
        policy: Policy | None = None,
        admin_ids: frozenset[int] = frozenset(),
        clock: Clock = utc_now,
    ) -> None:
        if any(type(value) is not int or value <= 0 for value in admin_ids):
            raise ValueError("Bootstrap administrators require positive integer Telegram IDs")
        self.repo = repo
        self.policy = policy or Policy()
        self.admin_ids = admin_ids
        self.clock = clock

    def _admin(self, state: BotState, actor: int) -> None:
        if not is_admin(state, actor, self.admin_ids):
            raise DomainError("forbidden", "Это действие доступно только ответственному")

    @staticmethod
    def _registered(state: BotState, tg_id: int) -> User:
        if type(tg_id) is not int or tg_id <= 0:
            raise DomainError("invalid_actor", "Некорректный Telegram ID")
        user = next((user for user in state.users.values() if user.tg_id == tg_id), None)
        if user is None:
            if any(
                request.tg_id == tg_id and request.status == RegistrationStatus.PENDING
                for request in state.registrations.values()
            ):
                raise DomainError("registration_pending", "Дождитесь одобрения привязки")
            raise DomainError("not_registered", "Откройте личный чат с ботом и выполните /reg")
        if user.role == Role.OUTSIDE:
            raise DomainError("outside", "Внесистемные не участвуют в бронировании")
        return user

    @staticmethod
    def _session(state: BotState, session_id: int) -> None:
        if type(session_id) is not int or session_id != state.session_id:
            raise DomainError("stale_session", "Это табло от предыдущей пары")

    @staticmethod
    def _subtask(state: BotState, number: str, label: str) -> Subtask:
        if number not in state.tasks or label not in state.tasks[number].subtasks:
            raise DomainError("task_not_found", "Подпункт не найден")
        return state.tasks[number].subtasks[label]

    @staticmethod
    def _release_all(state: BotState, uid: str) -> None:
        for task in state.tasks.values():
            for label, subtask in list(task.subtasks.items()):
                if subtask.reserved_by == uid:
                    task.subtasks[label] = Subtask()

    async def add_tasks(self, actor: int, tasks: Mapping[str, Sequence[str]]) -> Publication:
        try:
            additions = [
                Task(
                    number=number.strip(),
                    subtasks={label.strip().lower(): Subtask() for label in labels},
                )
                for number, labels in tasks.items()
            ]
        except ValidationError as exc:
            raise DomainError("invalid_task", "Проверьте номер и обозначения подпунктов") from exc
        if not additions or any(not task.subtasks for task in additions):
            raise DomainError("invalid_task", "Укажите номер и хотя бы один подпункт")

        def apply(state: BotState) -> Publication:
            if not is_admin(state, actor, self.admin_ids):
                self._registered(state, actor)
            added = 0
            for new_task in additions:
                current = state.tasks.setdefault(new_task.number, Task(number=new_task.number))
                for label in new_task.subtasks:
                    if label not in current.subtasks:
                        current.subtasks[label] = Subtask()
                        added += 1
            if not added:
                return Publication(state.session_id, 0, None)
            state.published_at = aware_time(self.clock())
            state.batch_id += 1
            event = Announcement(state.batch_id, eligible_priority_ids(state))
            return Publication(state.session_id, added, event)

        return await self.repo.transact(apply)

    async def book(self, actor: int, number: str, *, session_id: int) -> BookingResult:
        def apply(state: BotState) -> BookingResult:
            self._session(state, session_id)
            user = self._registered(state, actor)
            current = reservation_for(state, user.id)
            if current is not None:
                if current[0] == number:
                    state.tasks[number].subtasks[current[1]] = Subtask()
                    return BookingResult("cancelled", number, current[1])
                raise DomainError("already_reserved", "Сначала отмените текущую бронь")
            if number not in state.tasks:
                raise DomainError("task_not_found", "Номер не найден")
            free = sorted(
                label
                for label, sub in state.tasks[number].subtasks.items()
                if sub.status == SubtaskStatus.FREE
            )
            if not free:
                raise DomainError("task_full", "Номер уже полностью занят")
            now = aware_time(self.clock())
            gate = booking_gate(state, user, now, self.policy)
            if not gate.allowed:
                raise DomainError(gate.reason, f"Дождитесь своего окна: {gate.wait_seconds} сек.")
            label = free[0]
            state.tasks[number].subtasks[label] = Subtask(
                status=SubtaskStatus.RESERVED, reserved_by=user.id
            )
            state.last_booking_at = now
            return BookingResult("reserved", number, label)

        return await self.repo.transact(apply)

    async def confirm(
        self, actor: int, number: str, label: str, *, session_id: int, solver_id: str | None = None
    ) -> ConfirmationResult:
        def apply(state: BotState) -> ConfirmationResult:
            self._admin(state, actor)
            self._session(state, session_id)
            subtask = self._subtask(state, number, label)
            if subtask.status == SubtaskStatus.DONE:
                if solver_id is not None and solver_id != subtask.solved_by:
                    raise DomainError("already_completed", "Подпункт уже зачтён другому человеку")
                assert subtask.solved_by is not None
                return ConfirmationResult(False, subtask.solved_by)
            actual = solver_id if solver_id is not None else subtask.reserved_by
            if actual is None:
                raise DomainError("choose_solver", "Выберите фактического сдавшего")
            if actual not in state.users:
                raise DomainError("unknown_person", "Человек отсутствует в ведомости")
            state.tasks[number].subtasks[label] = Subtask(
                status=SubtaskStatus.DONE, solved_by=actual
            )
            self._release_all(state, actual)
            user = state.users[actual]
            before = user.outputs
            user.outputs += 1
            mark_pending(state, actual, "outputs", before, user.outputs)
            return ConfirmationResult(True, actual)

        return await self.repo.transact(apply)

    async def release(self, actor: int, number: str, label: str, *, session_id: int) -> bool:
        def apply(state: BotState) -> bool:
            self._admin(state, actor)
            self._session(state, session_id)
            subtask = self._subtask(state, number, label)
            if subtask.status != SubtaskStatus.RESERVED:
                return False
            state.tasks[number].subtasks[label] = Subtask()
            return True

        return await self.repo.transact(apply)

    async def clear(self, actor: int) -> int:
        def apply(state: BotState) -> int:
            self._admin(state, actor)
            state.tasks.clear()
            state.session_id += 1
            state.published_at = None
            state.last_booking_at = None
            return state.session_id

        return await self.repo.transact(apply)

    async def request_registration(
        self, tg_id: int, user_id: str, display_name: str
    ) -> RegistrationRequest:
        def apply(state: BotState) -> RegistrationRequest:
            if any(user.tg_id == tg_id for user in state.users.values()):
                raise DomainError("already_registered", "Аккаунт уже привязан")
            if user_id not in state.users:
                raise DomainError("unknown_person", "Выберите ФИО из ведомости")
            user = state.users[user_id]
            if user.role == Role.OUTSIDE:
                raise DomainError("outside", "Внесистемным не требуется привязка")
            if user.tg_id is not None:
                raise DomainError("already_bound", "Это ФИО уже привязано")
            for request in state.registrations.values():
                if request.tg_id == tg_id and request.status == RegistrationStatus.PENDING:
                    if request.user_id == user_id:
                        return request
                    raise DomainError("already_requested", "Другой запрос ещё ожидает решения")
            request = RegistrationRequest(
                id=uuid4().hex,
                user_id=user_id,
                tg_id=tg_id,
                display_name=display_name or f"TG {tg_id}",
            )
            state.registrations[request.id] = request
            return request

        return await self.repo.transact(apply)

    async def approve_registration(self, actor: int, request_id: str) -> None:
        def apply(state: BotState) -> None:
            self._admin(state, actor)
            request = state.registrations.get(request_id)
            if request is None:
                raise DomainError("request_not_found", "Заявка не найдена")
            if request.status == RegistrationStatus.APPROVED:
                return
            if request.status != RegistrationStatus.PENDING:
                raise DomainError("registration_closed", "Заявка уже закрыта")
            user = state.users[request.user_id]
            if user.role == Role.OUTSIDE:
                raise DomainError("outside", "Роль больше не допускает привязку")
            if user.tg_id is not None and user.tg_id != request.tg_id:
                raise DomainError("already_bound", "Это ФИО уже привязано")
            if any(u.tg_id == request.tg_id and u.id != user.id for u in state.users.values()):
                raise DomainError("already_registered", "Аккаунт уже привязан к другому ФИО")
            before = user.tg_id
            user.tg_id = request.tg_id
            request.status = RegistrationStatus.APPROVED
            if before != request.tg_id:
                mark_pending(state, user.id, "tg_id", before, request.tg_id)

        await self.repo.transact(apply)

    async def reject_registration(self, actor: int, request_id: str) -> None:
        def apply(state: BotState) -> None:
            self._admin(state, actor)
            request = state.registrations.get(request_id)
            if request is None:
                raise DomainError("request_not_found", "Заявка не найдена")
            if request.status == RegistrationStatus.APPROVED:
                raise DomainError("registration_closed", "Привязка уже одобрена")
            request.status = RegistrationStatus.REJECTED

        await self.repo.transact(apply)

    async def admin_snapshot(self, actor: int) -> BotState:
        state = await self.repo.snapshot()
        self._admin(state, actor)
        return state
