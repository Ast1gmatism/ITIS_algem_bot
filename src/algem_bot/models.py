from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    StringConstraints,
    model_validator,
)

Count = Annotated[int, Field(strict=True, ge=0)]
TelegramId = Annotated[int, Field(strict=True, gt=0)]
InternalId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
TaskNumber = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-zА-Яа-яЁё._-]{1,24}$")]
SubtaskLabel = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-zА-Яа-яЁё]{1,8}$")]
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
SourceField = Literal["fio", "role", "outputs", "tg_id"]
SourceValue = StrictInt | StrictStr | None
SOURCE_FIELDS: tuple[SourceField, ...] = ("fio", "role", "outputs", "tg_id")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Role(StrEnum):
    ADMIN = "Админ"
    STUDENT = "Студент"
    OUTSIDE = "Внесистемы"


class Category(StrEnum):
    PRIORITY = "priority"
    RESERVE = "reserve"
    BENCHMARK = "benchmark"
    OUTSIDE = "outside"

    @property
    def icon(self) -> str:
        return {
            Category.PRIORITY: "🟢",
            Category.RESERVE: "🟡",
            Category.BENCHMARK: "🔴",
            Category.OUTSIDE: "⚪",
        }[self]


class User(Model):
    id: InternalId
    fio: PersonName
    role: Role = Role.STUDENT
    outputs: Count = 0
    tg_id: TelegramId | None = None


class SheetRow(User):
    """Logical port record; `id` does NOT prescribe a physical Google column."""

    # Derived cells are untrusted presentation, not source fields.
    score: SourceValue = None
    category: Category | StrictStr | None = None

    @classmethod
    def from_user(cls, user: User) -> Self:
        return cls(**user.model_dump())

    def as_user(self) -> User:
        return User(
            id=self.id, fio=self.fio, role=self.role, outputs=self.outputs, tg_id=self.tg_id
        )


class SubtaskStatus(StrEnum):
    FREE = "free"
    RESERVED = "reserved"
    DONE = "done"


class Subtask(Model):
    status: SubtaskStatus = SubtaskStatus.FREE
    reserved_by: InternalId | None = None
    solved_by: InternalId | None = None

    @model_validator(mode="after")
    def valid_status(self) -> Self:
        if self.status == SubtaskStatus.FREE and (self.reserved_by or self.solved_by):
            raise ValueError("A free subtask cannot have an owner or a solver")
        if self.status == SubtaskStatus.RESERVED and (not self.reserved_by or self.solved_by):
            raise ValueError("A reserved subtask needs exactly one owner")
        if self.status == SubtaskStatus.DONE and (not self.solved_by or self.reserved_by):
            raise ValueError("A completed subtask needs exactly one solver")
        return self


class Task(Model):
    number: TaskNumber
    subtasks: dict[SubtaskLabel, Subtask] = Field(default_factory=dict)


class RegistrationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RegistrationRequest(Model):
    id: InternalId
    user_id: InternalId
    tg_id: TelegramId
    display_name: PersonName
    status: RegistrationStatus = RegistrationStatus.PENDING


class WriteAttempt(Model):
    revision: Count
    target: SourceValue


class PendingField(Model):
    base: SourceValue
    target: SourceValue
    revision: Count
    attempts: list[WriteAttempt] = Field(default_factory=list)


class SyncState(Model):
    change_seq: Count = 0
    snapshots: dict[InternalId, SheetRow] = Field(default_factory=dict)
    pending: dict[InternalId, dict[SourceField, PendingField]] = Field(default_factory=dict)
    last_error: str | None = None
    last_success_at: AwareDatetime | None = None
    missing_user_ids: list[InternalId] = Field(default_factory=list)


class BotState(Model):
    schema_version: Literal[1] = 1
    revision: Count = 0
    session_id: Annotated[int, Field(strict=True, gt=0)] = 1
    batch_id: Count = 0
    users: dict[InternalId, User] = Field(default_factory=dict)
    tasks: dict[TaskNumber, Task] = Field(default_factory=dict)
    registrations: dict[InternalId, RegistrationRequest] = Field(default_factory=dict)
    published_at: AwareDatetime | None = None
    last_booking_at: AwareDatetime | None = None
    sync: SyncState = Field(default_factory=SyncState)

    @model_validator(mode="after")
    def valid_references(self) -> Self:
        telegram_ids: set[int] = set()
        reserved: set[str] = set()
        for key, user in self.users.items():
            if key != user.id:
                raise ValueError("User key must match its internal ID")
            if user.tg_id is not None:
                if user.tg_id in telegram_ids:
                    raise ValueError("Telegram ID must be unique")
                telegram_ids.add(user.tg_id)
        for key, task in self.tasks.items():
            if key != task.number:
                raise ValueError("Task key must match its number")
            for subtask in task.subtasks.values():
                if subtask.solved_by is not None and subtask.solved_by not in self.users:
                    raise ValueError("Unknown solver")
                owner = subtask.reserved_by
                if owner is None:
                    continue
                if owner not in self.users or owner in reserved:
                    raise ValueError("Unknown owner or multiple reservations for one person")
                user = self.users[owner]
                if user.role == Role.OUTSIDE or user.tg_id is None:
                    raise ValueError("A reservation requires an active bound person")
                reserved.add(owner)
        pending_tg_ids: set[int] = set()
        for key, request in self.registrations.items():
            if key != request.id or request.user_id not in self.users:
                raise ValueError("Invalid registration reference")
            if request.status == RegistrationStatus.PENDING:
                if request.tg_id in pending_tg_ids:
                    raise ValueError("Only one pending request per Telegram account")
                pending_tg_ids.add(request.tg_id)
        for key, row in self.sync.snapshots.items():
            if key != row.id:
                raise ValueError("Invalid sheet snapshot ID")
        for uid, fields in self.sync.pending.items():
            if uid not in self.users:
                raise ValueError("Pending change references an unknown person")
            for field, pending in fields.items():
                if pending.target != source_value(self.users[uid], field):
                    raise ValueError("Pending target must match the current local field")
                if pending.revision > self.sync.change_seq:
                    raise ValueError("Pending revision exceeds the sequence")
                if any(attempt.revision > pending.revision for attempt in pending.attempts):
                    raise ValueError("An attempt cannot exceed its local revision")
        return self


def source_value(user: User, field: SourceField) -> SourceValue:
    if field == "role":
        return user.role.value
    if field == "fio":
        return user.fio
    if field == "outputs":
        return user.outputs
    return user.tg_id


def mark_pending(
    state: BotState, uid: str, field: SourceField, before: SourceValue, target: SourceValue
) -> None:
    fields = state.sync.pending.setdefault(uid, {})
    previous = fields.get(field)
    state.sync.change_seq += 1
    fields[field] = PendingField(
        base=previous.base if previous is not None else before,
        target=target,
        revision=state.sync.change_seq,
        attempts=[item.model_copy(deep=True) for item in previous.attempts] if previous else [],
    )
