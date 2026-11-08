"""The offline pilot's socket guard trips (GK B (33) P8)."""

import socket

import pytest

from smoke import accept_harness_pilot as pilot


@pytest.fixture
def guard(monkeypatch):
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, getattr(socket.socket, name))
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    pilot.install_guard()


def test_a_non_loopback_connect_is_refused(guard):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        with pytest.raises(pilot.NetworkRefused):
            s.connect(("192.0.2.1", 80))  # TEST-NET-1: never reached
        with pytest.raises(pilot.NetworkRefused):
            s.connect_ex(("198.51.100.1", 443))


def test_a_non_local_name_lookup_is_refused(guard):
    with pytest.raises(pilot.NetworkRefused):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(pilot.NetworkRefused):
        socket.create_connection(("example.com", 443), timeout=1)


def test_loopback_still_connects(guard):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2):
            pass
    assert socket.getaddrinfo("localhost", 80)
