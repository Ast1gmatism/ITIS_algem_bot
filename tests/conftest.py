import socket
from pathlib import Path

import pytest

from tests.helpers import Harness, build_harness


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Offline tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)


@pytest.fixture
async def h(tmp_path: Path) -> Harness:
    return await build_harness(tmp_path / "state.json")
