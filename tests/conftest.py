from pathlib import Path

import pytest

from tests.helpers import Harness, build_harness


@pytest.fixture
async def h(tmp_path: Path) -> Harness:
    return await build_harness(tmp_path / "state.json")
