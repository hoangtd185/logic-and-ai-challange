"""Shared test fixtures.

- ``no_network`` (autouse): unit tests can't open real connections, so no test can
  call Gemini by accident. Loopback stays allowed (asyncio on Windows needs it).
- ``mock_gemini``: a fake LLM client that answers by request kind (classification,
  extraction, second reading) and records every call.
"""

from __future__ import annotations

import copy
import socket
from typing import Any

import pytest

from medextract.extract import SECOND_READING
from medextract.llm.client import CallStats, LLMResponse

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    if request.node.get_closest_marker("live"):
        return
    real_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo

    def guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in _LOOPBACK and not host.startswith("127."):
            raise RuntimeError(f"unit tests must not use the network (tried {address!r}); mock Gemini instead")
        return real_connect(sock, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in _LOOPBACK and not str(host).startswith("127."):
            raise RuntimeError(f"unit tests must not use the network (tried to resolve {host!r})")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


class MockGemini:
    """Stands in for the Gemini client. Replies by request kind:

    - ``classification``: the classification reply (dict) or an exception to raise;
    - ``extraction``: the first extraction reply;
    - ``second_reading``: the scan's second reading (defaults to ``extraction``).

    A request with no reply configured fails the test.
    """

    model = "mock-gemini"

    def __init__(self, classification: Any = None, extraction: Any = None, second_reading: Any = None):
        self.replies = {
            "classification": classification,
            "extraction": extraction,
            "second_reading": second_reading if second_reading is not None else extraction,
        }
        self.calls: list[str] = []

    @staticmethod
    def kind(parts: list, schema: dict) -> str:
        if "evidence" in schema.get("properties", {}):
            return "classification"
        last = parts[-1] if parts else ""
        return "second_reading" if isinstance(last, str) and SECOND_READING in last else "extraction"

    def generate_json(self, *, system: str, parts: list, schema: dict) -> LLMResponse:
        kind = self.kind(parts, schema)
        self.calls.append(kind)
        reply = self.replies[kind]
        if reply is None:
            raise AssertionError(f"unexpected {kind} call to the model")
        if isinstance(reply, BaseException):
            raise reply
        return LLMResponse(copy.deepcopy(reply), CallStats(model=self.model, seconds=0.01))


@pytest.fixture
def mock_gemini():
    return MockGemini


def classified(doc_type: str, confidence: float = 0.95) -> dict:
    return {"evidence": f"looks like a {doc_type}", "document_type": doc_type, "confidence": confidence}


def field(value: Any, confidence: float = 0.95) -> dict:
    return {"value": value, "confidence": confidence}
