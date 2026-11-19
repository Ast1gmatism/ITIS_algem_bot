from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction

from algem_bot.config import Policy, aware_time
from algem_bot.models import BotState, Category, Role, User


@dataclass(frozen=True)
class Statistics:
    active_count: int
    total: int
    maximum: int

    @property
    def mean(self) -> float:
        return self.total / self.active_count if self.active_count else 0.0

    @property
    def gap(self) -> Fraction:
        if not self.active_count:
            return Fraction(0)
        return Fraction(self.maximum * self.active_count - self.total, self.active_count)


def statistics(users: Iterable[User]) -> Statistics:
    active = [user.outputs for user in users if user.role != Role.OUTSIDE]
    return Statistics(len(active), sum(active), max(active, default=0))


def score(outputs: int, maximum: int) -> int:
    if type(outputs) is not int or type(maximum) is not int or min(outputs, maximum) < 0:
        raise ValueError("Counts must be nonnegative integers")
    return min(10, (10 * outputs + maximum - 1) // maximum) if maximum else 0


def category(user: User, stats: Statistics) -> Category:
    if user.role == Role.OUTSIDE:
        return Category.OUTSIDE
    if user.outputs == 0 and stats.maximum <= 1:
        return Category.PRIORITY
    if user.outputs == stats.maximum:
        return Category.BENCHMARK
    if user.outputs == stats.maximum - 1:
        return Category.RESERVE
    return Category.PRIORITY


def is_planned_open(stats: Statistics, policy: Policy) -> bool:
    return stats.gap <= Fraction(policy.benchmark_raise_delta)


def reservation_for(state: BotState, uid: str) -> tuple[str, str] | None:
    for number, task in state.tasks.items():
        for label, subtask in task.subtasks.items():
            if subtask.reserved_by == uid:
                return number, label
    return None


def eligible_priority_ids(state: BotState) -> tuple[int, ...]:
    stats = statistics(state.users.values())
    return tuple(
        sorted(
            user.tg_id
            for user in state.users.values()
            if user.tg_id is not None
            and category(user, stats) == Category.PRIORITY
            and reservation_for(state, user.id) is None
        )
    )


def green_window_active(state: BotState, now: datetime, policy: Policy) -> bool:
    now = aware_time(now)
    return bool(
        state.published_at is not None
        and now < state.published_at + timedelta(seconds=policy.green_window_seconds)
        and eligible_priority_ids(state)
    )


def silence_anchor(state: BotState) -> datetime | None:
    values = [v for v in (state.published_at, state.last_booking_at) if v is not None]
    return max(values) if values else None


@dataclass(frozen=True)
class Gate:
    allowed: bool
    reason: str = ""
    wait_seconds: int = 0


def booking_gate(state: BotState, user: User, now: datetime, policy: Policy) -> Gate:
    now = aware_time(now)
    color = category(user, statistics(state.users.values()))
    if color == Category.OUTSIDE:
        return Gate(False, "outside")
    if color == Category.PRIORITY:
        return Gate(True)
    if green_window_active(state, now, policy):
        assert state.published_at is not None
        end = state.published_at + timedelta(seconds=policy.green_window_seconds)
        return Gate(False, "green_window", max(0, math.ceil((end - now).total_seconds())))
    if color == Category.RESERVE or is_planned_open(statistics(state.users.values()), policy):
        return Gate(True)
    anchor = silence_anchor(state)
    if anchor is not None:
        end = anchor + timedelta(seconds=policy.silence_timeout_seconds)
        wait = max(0, math.ceil((end - now).total_seconds()))
        if wait == 0:
            return Gate(True)
        return Gate(False, "benchmark_locked", wait)
    return Gate(False, "benchmark_locked", policy.silence_timeout_seconds)


def is_admin(state: BotState, tg_id: int, bootstrap_ids: frozenset[int]) -> bool:
    if type(tg_id) is not int or tg_id <= 0:
        return False
    bound = next((user for user in state.users.values() if user.tg_id == tg_id), None)
    if bound is not None:
        return bound.role == Role.ADMIN
    return tg_id in bootstrap_ids
