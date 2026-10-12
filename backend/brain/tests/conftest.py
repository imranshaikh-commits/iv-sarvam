"""Unit tests never reach the network (audit 2026-10-12, finding 12).

Every external call in the suite is meant to be mocked. A test that slips a
real request to Supabase or OpenRouter would pass on a developer machine with
keys in the environment and spend money, or fail in CI for a reason that has
nothing to do with the code. Any connection that is not to this machine fails
loudly instead.
"""
import socket

import pytest

_LOCAL = ("127.0.0.1", "::1", "localhost")
_real_connect = socket.socket.connect


def _guarded_connect(self, address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and host not in _LOCAL and not host.startswith("/"):
        raise RuntimeError(f"unit test tried to reach the network: {address!r}")
    return _real_connect(self, address, *args, **kwargs)


@pytest.fixture(autouse=True)
def _no_external_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
