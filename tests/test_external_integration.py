"""Installed-package runnable synthetic client checks over a real socket."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from test_kernel_transactions import _binary, _run, _server

from molt.kernel_client import KernelClient, KernelError

EXAMPLES = Path(os.environ.get("MOLT_EXAMPLE_DIR", Path(__file__).resolve().parents[1] / "examples"))
spec = importlib.util.spec_from_file_location("opaque_example", EXAMPLES / "opaque_client.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)
TS = "2026-11-19T00:00:00Z"
PAYLOAD = b'{"provenance":"synthetic","task_ref":"integration-1"}'


def test_external_client_submit_query_conflict_restart(tmp_path: Path) -> None:
    binary = _binary()
    state = tmp_path / "state"
    _run(binary, "init", "--state", state)
    with _server(binary, state) as socket:
        first = example.submit(socket, "synthetic-integration-1", PAYLOAD, TS)
        assert first["generation"] == 1
        assert example.query(socket, "synthetic-integration-1")["result"] == first
        assert example.submit(socket, "synthetic-integration-1", PAYLOAD, TS) == first
        with pytest.raises(KernelError, match="request_conflict"):
            example.submit(socket, "synthetic-integration-1", PAYLOAD + b" ", TS)
    with _server(binary, state) as socket:
        assert example.query(socket, "synthetic-integration-1")["result"] == first
        assert example.submit(socket, "synthetic-integration-1", PAYLOAD, TS) == first
    _run(binary, "audit", "--state", state)


def test_external_client_lost_response_stays_unknown(tmp_path: Path, monkeypatch) -> None:
    binary = _binary()
    state = tmp_path / "state"
    _run(binary, "init", "--state", state)
    original = KernelClient.call

    def lost_response(self, method, params=None):
        result = original(self, method, params)
        if method == "generation.commit":
            raise ConnectionError("synthetic lost response after durable commit")
        return result

    with _server(binary, state) as socket:
        with monkeypatch.context() as patch:
            patch.setattr(KernelClient, "call", lost_response)
            with pytest.raises(example.OutcomeUnknown, match="synthetic-unknown"):
                example.submit(socket, "synthetic-unknown", PAYLOAD, TS)
        status = example.query(socket, "synthetic-unknown")
        assert status["status"] == "committed"
        assert status["result"]["generation"] == 1
    _run(binary, "audit", "--state", state)
