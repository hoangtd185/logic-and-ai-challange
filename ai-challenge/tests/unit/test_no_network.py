"""The autouse guard in conftest.py: unit tests cannot reach the network, so they cannot call Gemini."""

import socket

import pytest


def test_outbound_connection_is_blocked():
    with pytest.raises(RuntimeError, match="must not use the network"):
        socket.create_connection(("generativelanguage.googleapis.com", 443), timeout=1)


def test_dns_lookup_is_blocked():
    with pytest.raises(RuntimeError, match="must not use the network"):
        socket.getaddrinfo("generativelanguage.googleapis.com", 443)


def test_loopback_still_allowed():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket()
    try:
        client.connect(server.getsockname())  # no RuntimeError
    finally:
        client.close()
        server.close()
