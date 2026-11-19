from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Clock = Callable[[], datetime]


class Policy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    green_window_seconds: Annotated[int, Field(strict=True, ge=0)] = 20
    silence_timeout_seconds: Annotated[int, Field(strict=True, gt=0)] = 60
    benchmark_raise_delta: Annotated[Decimal, Field(ge=0)] = Decimal("0.5")


def utc_now() -> datetime:
    return datetime.now(UTC)


def aware_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware clock is required")
    return value.astimezone(UTC)
