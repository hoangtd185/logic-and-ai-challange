"""Batch runner: run state, --only-failed, early stop on network failures. No network."""

import json
from pathlib import Path

import pytest

from medextract.batch import RunState, run_batch
from medextract.ingest import IngestError
from medextract.llm.client import LLMNetworkError
from medextract.pipeline import PipelineOutput
from medextract.schemas import unknown_result

MODEL = "m-1"


def ok_output(seconds=12.0, cached=False):
    call = {"model": MODEL, "seconds": seconds, "attempts": [{"seconds": seconds}], "http_requests": 1,
            "request_bytes": 150_000, "usage": {"total_input_tokens": 1800, "total_output_tokens": 60,
                                                 "total_thought_tokens": 300}, "cached": False, "original": None}
    if cached:
        call = {"model": MODEL, "seconds": 0.0, "attempts": [], "http_requests": 0, "request_bytes": 0,
                "usage": None, "cached": True, "original": call}
    trace = {"model": MODEL, "pages": 1, "seconds": seconds,
             "classification": {"raw": {}, "call": call}, "extraction": None}
    return PipelineOutput(unknown_result(0.9), trace)


class Process:
    """Plays back an outcome per file name: 'ok', 'net' (network failure), 'ingest', or 'cached'."""

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.seen = []

    def __call__(self, path):
        self.seen.append(path.stem)
        outcome = self.outcomes.get(path.stem, "ok")
        if outcome == "net":
            raise LLMNetworkError("Gemini call failed after 5 attempt(s): [Errno 11001] getaddrinfo failed")
        if outcome == "ingest":
            raise IngestError(f"{path.name}: unsupported file type")
        return ok_output(cached=outcome == "cached")


@pytest.fixture
def docs(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    names = ["a", "b", "c", "d", "e", "f"]
    for n in names:
        (folder / f"{n}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return [folder / f"{n}.png" for n in names]


def runs(out):
    return json.loads((out / "runs.json").read_text(encoding="utf-8"))["documents"]


def test_records_every_document(docs, tmp_path):
    out = tmp_path / "results"
    report = run_batch(docs, Process({"c": "ingest"}), out, MODEL, echo=lambda s: None)
    state = runs(out)
    assert report.succeeded == ["a", "b", "d", "e", "f"] and report.failed == ["c"]
    assert state["c"]["status"] == "failed" and state["c"]["error_kind"] == "ingest"
    assert state["a"]["calls"][0]["seconds"] == 12.0
    assert state["a"]["calls"][0]["usage"]["total_thought_tokens"] == 300
    assert (out / "a.json").is_file() and (out / "raw" / "a.json").is_file()
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "| a.png | ok | unknown | 0.90 |" in summary and "12.0s" in summary and "**failed**" in summary


def test_stops_after_consecutive_network_failures(docs, tmp_path):
    out = tmp_path / "results"
    process = Process({"b": "net", "c": "net", "d": "net"})
    report = run_batch(docs, process, out, MODEL, max_consecutive_network_failures=3, echo=lambda s: None)
    assert report.stopped_early
    assert process.seen == ["a", "b", "c", "d"]  # e and f never attempted
    assert report.not_attempted == ["e", "f"]
    state = runs(out)
    assert state["e"]["status"] == "skipped" and state["f"]["status"] == "skipped"


def test_non_network_failure_resets_the_streak(docs, tmp_path):
    process = Process({"a": "net", "b": "net", "c": "ingest", "d": "net", "e": "net"})
    report = run_batch(docs, process, tmp_path / "r", MODEL, max_consecutive_network_failures=3, echo=lambda s: None)
    assert not report.stopped_early and process.seen == ["a", "b", "c", "d", "e", "f"]


def test_only_failed_reruns_failed_and_skipped(docs, tmp_path):
    out = tmp_path / "results"
    run_batch(docs, Process({"b": "net", "c": "net", "d": "net"}), out, MODEL, echo=lambda s: None)

    second = Process({})
    report = run_batch(docs, second, out, MODEL, only_failed=True, echo=lambda s: None)
    assert second.seen == ["b", "c", "d", "e", "f"]
    assert report.skipped_ok == ["a"]
    assert all(r["status"] == "ok" for r in runs(out).values())


def test_only_failed_reruns_everything_for_a_new_model(docs, tmp_path):
    out = tmp_path / "results"
    run_batch(docs, Process({}), out, MODEL, echo=lambda s: None)
    process = Process({})
    run_batch(docs, process, out, "m-2", only_failed=True, echo=lambda s: None)
    assert len(process.seen) == len(docs)


def test_only_failed_adopts_results_written_before_tracking(docs, tmp_path):
    out = tmp_path / "results"
    (out / "raw").mkdir(parents=True)
    result = unknown_result(0.9).model_dump(mode="json")
    for stem, model in [("a", MODEL), ("b", "older-model")]:
        (out / f"{stem}.json").write_text(json.dumps(result), encoding="utf-8")
        (out / "raw" / f"{stem}.json").write_text(json.dumps({"model": model, "extraction": None}), encoding="utf-8")

    process = Process({})
    run_batch(docs, process, out, MODEL, only_failed=True, echo=lambda s: None)
    assert "a" not in process.seen  # adopted
    assert "b" in process.seen  # different model: redone
    assert "adopted" in runs(out)["a"]["note"]


def test_cached_calls_report_original_timing(docs, tmp_path):
    out = tmp_path / "results"
    run_batch(docs[:1], Process({"a": "cached"}), out, MODEL, echo=lambda s: None)
    call = runs(out)["a"]["calls"][0]
    assert call["cached"] is True and call["seconds"] == 12.0


def test_state_survives_reload(tmp_path):
    state = RunState(tmp_path)
    state.record("x", {"file": "x.pdf", "status": "failed", "model": MODEL, "error_kind": "network", "error": "boom"})
    again = RunState(tmp_path)
    assert again.documents["x"]["status"] == "failed"
    assert "boom" in (tmp_path / "summary.md").read_text(encoding="utf-8")
