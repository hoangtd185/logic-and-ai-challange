"""Batch behaviour on a slow connection: progress output, the scan double-check's small
retry budget, and 'partial' results that --only-failed picks up again. Gemini is mocked."""

import contextlib
import json
import logging
from pathlib import Path

import pytest

import medextract.pipeline as pipeline
from conftest import classified, field
from medextract.batch import RunState, check_failed, run_batch
from medextract.config import SECOND_READING_MAX_RETRIES
from medextract.ingest import load_document
from medextract.llm.client import CachingClient, GeminiClient, LLMNetworkError
from medextract.pipeline import process_document, process_file

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "documents"
SCAN = SAMPLES / "lab_02.png"

LAB = {
    "lab_name": field("Mekong Clinical Laboratory"), "patient_name": field("Le Thi Hoa"), "date": field("03/08/2024"),
    "tests": field([{"test_name": field("Hemoglobin"), "result": field(11.2), "unit": field("g/dL"),
                     "reference_range": field("12.0-15.5"), "flag": field("low")}]),
}
TIMEOUT = LLMNetworkError("Gemini call failed after 2 attempt(s): Request timed out. This is a client-side timeout.")


class RetryAware:
    """Mock client that also records the retry budget the pipeline asks for."""

    def __init__(self, mock):
        self.mock, self.model, self.budgets = mock, mock.model, []

    @contextlib.contextmanager
    def limited_retries(self, n):
        self.budgets.append(n)
        yield

    def generate_json(self, **kwargs):
        return self.mock.generate_json(**kwargs)


# --- progress ------------------------------------------------------------------------------------


def test_each_step_reports_when_it_finishes(mock_gemini):
    lines = []
    process_document(load_document(SCAN), mock_gemini(classified("lab_report", 0.99), LAB), progress=lines.append)
    steps = [line.split()[0] for line in lines]
    assert steps == ["classify", "extract", "re-read"]
    assert "→ lab_report (0.99)" in lines[0]


def test_slow_call_announces_itself(mock_gemini, monkeypatch):
    """A call still running after the first-notice delay says so, then keeps reporting."""
    import threading

    monkeypatch.setattr(pipeline, "PROGRESS_FIRST_NOTICE_S", 0.01)
    monkeypatch.setattr(pipeline, "PROGRESS_HEARTBEAT_S", 0.01)
    gate = threading.Event()
    lines = []

    class Slow:
        model = "mock"

        def generate_json(self, **kwargs):
            gate.wait(0.2)  # the call is "in flight" for a moment
            return mock_gemini(classified("unknown")).generate_json(**kwargs)

    process_document(load_document(SCAN), Slow(), progress=lines.append)
    assert any("classify" in line and "waiting for Gemini" in line for line in lines)
    assert any("still waiting" in line for line in lines)


def test_no_progress_means_no_threads_or_output(mock_gemini, capsys):
    process_document(load_document(SCAN), mock_gemini(classified("lab_report"), LAB))
    assert capsys.readouterr().out == ""


def test_retry_warnings_name_the_step(caplog):
    from medextract.llm.client import CALL_LABEL

    class Flaky(GeminiClient):
        def __init__(self):  # no SDK needed
            self.model, self.max_retries, self.backoff_s = "m", 1, 0.0
            self.deadline_s, self.overload_backoff_s = None, 0.0
            self._sleep, self._http_log, self.calls = (lambda s: None), [], 0

        def _call(self, system, parts, schema):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionResetError("Server disconnected without sending a response.")
            return '{"ok": true}', None

    token = CALL_LABEL.set("re-read scan")
    try:
        with caplog.at_level(logging.WARNING):
            Flaky().generate_json(system="s", parts=["x"], schema={})
    finally:
        CALL_LABEL.reset(token)
    assert "re-read scan: Gemini network error (Server disconnected" in caplog.text
    assert "(attempt 2 of 2)" in caplog.text


# --- the second reading's retry budget --------------------------------------------------------------


def test_second_reading_asks_for_a_small_retry_budget(mock_gemini):
    client = RetryAware(mock_gemini(classified("lab_report"), LAB))
    process_document(load_document(SCAN), client)
    assert client.budgets == [SECOND_READING_MAX_RETRIES] == [1]


def test_limited_retries_lowers_then_restores():
    client = GeminiClient.__new__(GeminiClient)
    client.max_retries = 4
    with client.limited_retries(1):
        assert client.max_retries == 1
    assert client.max_retries == 4
    with client.limited_retries(9):  # never raises the budget
        assert client.max_retries == 4


def test_cache_passes_the_budget_to_the_real_client(tmp_path):
    inner = GeminiClient.__new__(GeminiClient)
    inner.max_retries, inner.model = 4, "m"
    with CachingClient(inner, tmp_path).limited_retries(1):
        assert inner.max_retries == 1
    assert inner.max_retries == 4


def test_failed_second_reading_keeps_the_document(mock_gemini):
    lines = []
    model = mock_gemini(classified("lab_report"), LAB, TIMEOUT)
    out = process_document(load_document(SCAN), model, progress=lines.append)
    assert out.result.fields.lab_name.value == "Mekong Clinical Laboratory"
    assert out.trace["confidence"]["method"] == "model_only"
    assert "timed out" in out.trace["second_reading"]["error"]
    assert any("re-read" in line and "failed (timed out)" in line for line in lines)


# --- partial results and --only-failed ----------------------------------------------------------------


def test_failed_double_check_is_partial_and_rerun(mock_gemini, tmp_path):
    out_dir = tmp_path / "results"
    flaky = mock_gemini(classified("lab_report"), LAB, TIMEOUT)
    report = run_batch([SCAN], lambda p: process_file(p, flaky), out_dir, flaky.model, echo=lambda s: None)
    assert report.partial == ["lab_02"]
    record = json.loads((out_dir / "runs.json").read_text(encoding="utf-8"))["documents"]["lab_02"]
    assert record["status"] == "partial"
    assert "| lab_02.png | **partial** |" in (out_dir / "summary.md").read_text(encoding="utf-8")

    healthy = mock_gemini(classified("lab_report"), LAB)  # connection is back
    report = run_batch([SCAN], lambda p: process_file(p, healthy), out_dir, healthy.model,
                       only_failed=True, echo=lambda s: None)
    assert report.processed == ["lab_02"] and report.partial == []
    assert "second_reading" in healthy.calls
    record = json.loads((out_dir / "runs.json").read_text(encoding="utf-8"))["documents"]["lab_02"]
    assert record["status"] == "ok" and record["confidence_method"] == "double_reading"


def test_records_from_before_partial_existed_are_recognised(tmp_path):
    """Your current runs.json: lab_02 is 'ok' but its notes say the second reading failed."""
    (tmp_path / "lab_02.json").write_text("{}", encoding="utf-8")
    state = RunState(tmp_path)
    state.documents["lab_02"] = {
        "status": "ok", "model": "m",
        "confidence_notes": ["second reading failed: Gemini call failed after 5 attempt(s): Request timed out."],
    }
    assert check_failed(state.documents["lab_02"])
    assert not state.is_done(Path("lab_02.png"), "m")


def test_complete_documents_are_still_skipped(mock_gemini, tmp_path):
    model = mock_gemini(classified("lab_report"), LAB)
    run_batch([SCAN], lambda p: process_file(p, model), tmp_path, model.model, echo=lambda s: None)
    again = mock_gemini(classified("lab_report"), LAB)
    report = run_batch([SCAN], lambda p: process_file(p, again), tmp_path, again.model,
                       only_failed=True, echo=lambda s: None)
    assert report.processed == [] and report.skipped_ok == ["lab_02"] and again.calls == []
