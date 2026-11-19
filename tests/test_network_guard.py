import socket

import pytest


def test_external_connections_are_blocked_by_default() -> None:
    with pytest.raises(AssertionError, match="Offline tests"):
        socket.create_connection(("example.invalid", 443))
