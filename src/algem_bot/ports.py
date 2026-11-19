from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from algem_bot.models import Category, SheetRow, SourceField, SourceValue


@dataclass(frozen=True)
class SheetPatch:
    user_id: str
    fields: dict[SourceField, SourceValue] = field(default_factory=dict)
    revisions: dict[SourceField, int] = field(default_factory=dict)
    score: int | None = None
    category: Category | None = None


class SheetPort(Protocol):
    """Logical adapter boundary; no physical sheet columns are assumed."""

    async def read_rows(self) -> list[SheetRow]: ...

    async def write_rows(self, patches: list[SheetPatch]) -> None: ...
