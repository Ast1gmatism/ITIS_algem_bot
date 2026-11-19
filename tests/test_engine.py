from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from algem_bot.config import Policy
from algem_bot.engine import category, is_planned_open, score, statistics
from algem_bot.models import Category, Role, User


@pytest.mark.parametrize(
    ("outputs", "maximum", "expected"),
    [(0, 0, 0), (0, 3, 0), (1, 3, 4), (2, 3, 7), (3, 3, 10), (1, 20, 1), (99, 3, 10)],
)
def test_ac03_round_up_and_zero_guard(outputs: int, maximum: int, expected: int) -> None:
    assert score(outputs, maximum) == expected


@given(st.integers(0, 100_000), st.integers(0, 100_000))
def test_scores_always_stay_in_original_range(outputs: int, maximum: int) -> None:
    assert 0 <= score(outputs, maximum) <= 10


@given(st.integers(1, 100_000), st.integers(0, 100_000))
def test_score_matches_integer_ceiling_for_valid_relative_counts(maximum: int, raw: int) -> None:
    outputs = min(raw, maximum)
    assert score(outputs, maximum) == (10 * outputs + maximum - 1) // maximum


def test_d02_only_active_define_maximum_and_mean() -> None:
    users = [
        User(id="a", fio="Студент А", outputs=4),
        User(id="b", fio="Студент Б", outputs=2, role=Role.ADMIN),
        User(id="x", fio="Студент В", outputs=9, role=Role.OUTSIDE),
    ]
    result = statistics(users)
    assert (result.maximum, result.mean, result.active_count) == (4, 3.0, 2)


def test_ac11_no_active_people() -> None:
    result = statistics([User(id="x", fio="Студент А", role=Role.OUTSIDE, outputs=2)])
    assert (result.maximum, result.mean, result.active_count) == (0, 0.0, 0)


@pytest.mark.parametrize(
    ("outputs", "maximum", "expected"),
    [
        (0, 0, Category.PRIORITY),
        (0, 1, Category.PRIORITY),
        (1, 1, Category.BENCHMARK),
        (0, 2, Category.PRIORITY),
        (1, 2, Category.RESERVE),
        (2, 2, Category.BENCHMARK),
    ],
)
def test_startup_exception_precedes_regular_colors(
    outputs: int, maximum: int, expected: Category
) -> None:
    user = User(id="a", fio="Студент А", outputs=outputs)
    leader = User(id="b", fio="Студент Б", outputs=maximum)
    assert category(user, statistics([user, leader])) == expected


def test_outside_always_has_white_category() -> None:
    user = User(id="x", fio="Студент А", role=Role.OUTSIDE)
    assert category(user, statistics([user])) == Category.OUTSIDE


def test_planned_threshold_is_inclusive_without_float_error() -> None:
    users = [User(id=f"u{i}", fio=f"Студент {i}", outputs=n) for i, n in enumerate([4, 4, 4, 2])]
    stats = statistics(users)
    assert is_planned_open(stats, Policy(benchmark_raise_delta=Decimal("0.5")))
    assert not is_planned_open(stats, Policy(benchmark_raise_delta=Decimal("0.49")))
