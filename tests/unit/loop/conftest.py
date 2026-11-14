"""Fixtures for the loop tests that must run with no ambient effect."""
import socket
import subprocess
import threading
import time

import pytest


@pytest.fixture
def sealed(monkeypatch):
    """Fail the test when this thread sleeps, reads a real clock, spawns a process or opens a network connection."""
    owner = threading.get_ident()

    def refuse(what, real):
        def guarded(*args, **kwargs):
            if threading.get_ident() != owner: return real(*args, **kwargs)  # a runner thread is not the simulation
            raise AssertionError(f'the simulation reached {what}')
        return guarded

    for module, name in ((time, 'sleep'), (time, 'time'), (time, 'monotonic'), (subprocess.Popen, '__init__'),
                         (socket.socket, 'connect'), (socket, 'getaddrinfo')):
        monkeypatch.setattr(module, name, refuse(f'{module.__name__}.{name}', getattr(module, name)))
