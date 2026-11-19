from __future__ import annotations

import copy

from algem_bot.models import SheetRow
from algem_bot.ports import SheetPatch


class MemorySheet:
    """Deterministic offline adapter, not a Google Sheets implementation."""

    def __init__(self, rows: list[SheetRow]) -> None:
        if len({row.id for row in rows}) != len(rows):
            raise ValueError("Duplicate logical sheet record ID")
        self.rows = {row.id: row.model_copy(deep=True) for row in rows}
        self.write_calls: list[list[SheetPatch]] = []
        self.fail_read = False
        self.fail_write = False

    async def read_rows(self) -> list[SheetRow]:
        if self.fail_read:
            raise OSError("Synthetic read failure")
        return [row.model_copy(deep=True) for row in self.rows.values()]

    async def write_rows(self, patches: list[SheetPatch]) -> None:
        if self.fail_write:
            raise OSError("Synthetic write failure")
        # Validate the whole batch before publishing any part of it.
        updated = {uid: row.model_copy(deep=True) for uid, row in self.rows.items()}
        for patch in patches:
            if patch.user_id not in updated:
                raise KeyError("A row disappeared; refusing to recreate it implicitly")
            payload = updated[patch.user_id].model_dump()
            for key, value in patch.fields.items():
                payload[key] = value
            if patch.score is not None:
                payload["score"] = patch.score
            if patch.category is not None:
                payload["category"] = patch.category
            updated[patch.user_id] = SheetRow.model_validate(payload)
        self.rows = updated
        self.write_calls.append(copy.deepcopy(patches))
