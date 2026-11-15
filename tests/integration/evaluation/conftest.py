import socket

import pytest


@pytest.fixture
def listener():
    """A local TCP port that accepts connections, so a network canary can tell reachable from blocked."""
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen()
    yield server.getsockname()[1]
    server.close()
