"""Score pipeline results against the ground truth.

    python -m medextract.evaluate                       # results/ vs samples/
    python -m medextract.evaluate --results results --samples samples

What is measured:

* Classification: predicted ``document_type`` vs expected, including the
  non-medical sample, which must come out ``unknown``.
* Fields: every leaf field, including each row of items / tests / medications.
  A field is correct when:
    - expected null, got null                     (correct null)
    - expected a value, got the same value        (correct)
  and wrong when:
    - expected null, got a value                  (hallucinated)
    - expected a value, got null                  (missed)
    - expected a value, got a different value     (wrong value)
  Dates are compared as ISO dates ("12/03/2024" == "2024-03-12"); an impossible
  printed date (30/02/2024) must be returned as printed. Numbers match within
  0.01. Text matches after Unicode / case / whitespace / punctuation
  normalisation; long text (>= 40 chars) also matches at >= 90% similarity.
  Rows are paired by their name field (description / test_name / name), so
  order doesn't matter; a predicted row with no counterpart counts as
  hallucinated, an expected row with none as missed.
* Validation: expected vs actual (field, code) pairs in ``validation_errors``.
* Confidence: is it meaningful? Mean confidence of correct vs wrong values,
  plus the chance that a random correct value scores above a random wrong one,
  for both Gemini's own score (from the trace) and the final calibrated score.
* Results produced before validation / confidence calibration existed (their
  trace has no ``validation`` step) are marked *predates validation*: their
  fields are scored, but they are left out of the validation and calibrated-
  confidence numbers instead of counting as misses.

Writes results/eval.json (every field, for inspection) and results/eval_report.md,
and prints a summary table. Uses only the standard library, so it runs without
the pipeline's dependencies.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from medextract.parsing import normalize_text, parse_date

ROW_KEYS = {"items": "description", "tests": "test_name", "medications": "name"}
CORRECT = {"correct", "correct_null"}
LONG_TEXT = 40
TEXT_SIMILARITY = 0.9
NUMBER_TOLERANCE = 0.01


@dataclass
class FieldResult:
    document: str
    document_type: str
    field: str  # concrete path, e.g. items[2].total
    group: str  # generic path, e.g. items[].total
    expected: Any
    predicted: Any
    confidence: float | None
    outcome: str  # correct, correct_null, hallucinated, missed, wrong_value

    @property
    def correct(self) -> bool:
        return self.outcome in CORRECT


@dataclass
class DocumentResult:
    document: str
    expected_type: str
    predicted_type: str | None
    type_confidence: float | None
    status: str  # evaluated, not_run
    fields: list[FieldResult] = field(default_factory=list)
    predates_validation: bool = False  # produced before validation/calibration existed
    model_scores: list[FieldResult] = field(default_factory=list)  # same fields, Gemini's own confidence
    validation_expected: list[list[str]] = field(default_factory=list)
    validation_actual: list[list[str]] = field(default_factory=list)

    @property
    def type_correct(self) -> bool:
        return self.predicted_type == self.expected_type


# --- flattening ---------------------------------------------------------------------------


def is_date_field(name: str) -> bool:
    return name == "date" or name.endswith("_date")


def flatten_truth(fields: dict[str, Any]) -> dict[str, Any]:
    """Ground truth → {path: value}. Rows stay as lists under their parent path."""
    flat: dict[str, Any] = {}
    for name, value in fields.items():
        if name in ROW_KEYS and isinstance(value, list):
            flat[name] = value
        elif isinstance(value, dict) and not ("printed" in value and "iso" in value):
            for k, v in value.items():
                flat[f"{name}.{k}"] = v
        elif value is None and name == "diagnosis":
            flat["diagnosis.primary"] = flat["diagnosis.secondary"] = None
        else:
            flat[name] = value
    return flat


def flatten_prediction(fields: dict[str, Any]) -> dict[str, tuple[Any, float | None]]:
    """Output envelope fields → {path: (value, confidence)}; rows as lists of such dicts."""
    flat: dict[str, Any] = {}
    for name, f in fields.items():
        value, conf = f.get("value"), f.get("confidence")
        if name in ROW_KEYS and isinstance(value, list):
            flat[name] = ([{k: (v.get("value"), v.get("confidence")) for k, v in row.items()} for row in value], conf)
        elif isinstance(value, dict):
            for k, v in value.items():
                flat[f"{name}.{k}"] = (v.get("value"), v.get("confidence"))
        elif value is None and name == "diagnosis":
            flat["diagnosis.primary"] = flat["diagnosis.secondary"] = (None, conf)
        else:
            flat[name] = (value, conf)
    return flat


# --- value comparison --------------------------------------------------------------------------


def _text(v: Any) -> str:
    """Comparable form: Unicode/case/whitespace-normalised, punctuation dropped."""
    return " ".join(re.sub(r"[^\w\s]", " ", normalize_text(str(v))).split())


def values_match(expected: Any, predicted: Any, name: str) -> bool:
    if is_date_field(name.rsplit(".", 1)[-1]):
        if isinstance(expected, dict):  # {"printed", "iso"}
            if expected.get("iso"):
                return parse_date(str(predicted)).iso == expected["iso"]
            return _text(expected["printed"]) == _text(predicted)  # impossible date: as printed
        return parse_date(str(predicted)).iso == parse_date(str(expected)).iso
    if isinstance(expected, bool) or isinstance(predicted, bool):
        return expected == predicted
    if isinstance(expected, (int, float)) and isinstance(predicted, (int, float)):
        return abs(expected - predicted) <= NUMBER_TOLERANCE
    if isinstance(expected, (int, float)) or isinstance(predicted, (int, float)):
        try:
            return abs(float(expected) - float(predicted)) <= NUMBER_TOLERANCE
        except (TypeError, ValueError):
            return False
    if isinstance(expected, list) or isinstance(predicted, list):
        if not (isinstance(expected, list) and isinstance(predicted, list)):
            return False
        return sorted(map(_text, expected)) == sorted(map(_text, predicted))
    a, b = _text(expected), _text(predicted)
    if a == b:
        return True
    long_a, long_b = normalize_text(str(expected)), normalize_text(str(predicted))
    if min(len(long_a), len(long_b)) >= LONG_TEXT:
        return difflib.SequenceMatcher(None, long_a, long_b).ratio() >= TEXT_SIMILARITY
    return False


def outcome(expected: Any, predicted: Any, name: str) -> str:
    if expected is None:
        return "correct_null" if predicted is None else "hallucinated"
    if predicted is None:
        return "missed"
    return "correct" if values_match(expected, predicted, name) else "wrong_value"


# --- rows ---------------------------------------------------------------------------------------


def pair_rows(expected: list[dict], predicted: list[dict], key: str) -> list[tuple[int | None, int | None]]:
    """Greedy best-match pairing of row indices on the rows' name field; leftovers paired with None."""
    def name(row, is_pred):
        v = row.get(key)
        v = v[0] if is_pred and isinstance(v, tuple) else v
        return normalize_text(str(v or ""))

    candidates = []
    for i, e in enumerate(expected):
        for j, p in enumerate(predicted):
            score = difflib.SequenceMatcher(None, name(e, False), name(p, True)).ratio()
            if score >= 0.6:
                candidates.append((score, -abs(i - j), i, j))
    pairs, used_e, used_p = [], set(), set()
    for score, _, i, j in sorted(candidates, reverse=True):
        if i not in used_e and j not in used_p:
            pairs.append((i, j))
            used_e.add(i)
            used_p.add(j)
    out: list[tuple[int | None, int | None]] = sorted(pairs)
    out += [(i, None) for i in range(len(expected)) if i not in used_e]
    out += [(None, j) for j in range(len(predicted)) if j not in used_p]
    return out


# --- evaluating one document ---------------------------------------------------------------------


def evaluate_document(
    doc_id: str, truth: dict[str, Any], result: dict[str, Any] | None, trace: dict[str, Any] | None = None
) -> DocumentResult:
    expected_type = truth["document_type"]
    validation_expected = sorted([e["field"], e["code"]] for e in truth.get("expected_validation_errors", []))
    if result is None:
        return DocumentResult(doc_id, expected_type, None, None, "not_run", validation_expected=validation_expected)

    doc = DocumentResult(
        doc_id, expected_type, result.get("document_type"), result.get("confidence"), "evaluated",
        validation_expected=validation_expected,
        validation_actual=sorted([i["field"], i["code"]] for i in result.get("validation_errors", [])),
        predates_validation=trace is not None and trace.get("extraction") is not None and "validation" not in trace,
    )
    exp = flatten_truth(truth["fields"])
    # Wrong type: the expected fields were not extracted. Score them against an
    # empty prediction (so values count as missed, nulls as correct).
    pred = flatten_prediction(result.get("fields", {})) if doc.type_correct else {}

    def add(path, group, expected, predicted, conf):
        doc.fields.append(FieldResult(doc_id, expected_type, path, group, expected, predicted, conf,
                                      outcome(expected, predicted, group)))

    for name, expected in exp.items():
        if name in ROW_KEYS:
            rows_pred, _ = pred.get(name, ([], None))
            rows_pred = rows_pred or []
            for i, j in pair_rows(expected or [], rows_pred, ROW_KEYS[name]):
                e_row = expected[i] if i is not None else None
                p_row = rows_pred[j] if j is not None else None
                keys = list((e_row or {}).keys()) or list((p_row or {}).keys())
                index = i if i is not None else f"extra{j}"
                for k in keys:
                    e_val = e_row.get(k) if e_row else None
                    p_val, p_conf = p_row.get(k, (None, None)) if p_row else (None, None)
                    if e_row is None and p_val is None:
                        continue  # empty cells of an invented row add nothing
                    add(f"{name}[{index}].{k}", f"{name}[].{k}", e_val, p_val, p_conf)
            continue
        p_val, p_conf = pred.get(name, (None, None))
        add(name, name, expected, p_val, p_conf)

    if not doc.type_correct:
        # Values extracted under the wrong type don't belong to this document: made up.
        for path, (value, conf) in flatten_prediction(result.get("fields", {})).items():
            rows = value if path in ROW_KEYS and isinstance(value, list) else None
            if rows is not None:
                for j, row in enumerate(rows):
                    for k, (v, c) in row.items():
                        if v is not None:
                            add(f"{path}[extra{j}].{k}", f"wrong-type:{path}[].{k}", None, v, c)
            elif value is not None:
                add(path, f"wrong-type:{path}", None, value, conf)

    # Gemini's own scores, from the raw reply in the trace, to compare with the calibrated ones.
    raw = ((trace or {}).get("extraction") or {}).get("raw")
    if isinstance(raw, dict) and doc.type_correct:
        try:
            doc.model_scores = evaluate_document(doc_id, truth, {**result, "fields": raw}).fields
        except (AttributeError, TypeError, KeyError):  # a malformed reply (extraction failed)
            doc.model_scores = []
    return doc


# --- aggregation ------------------------------------------------------------------------------------


def _rate(items: list[FieldResult]) -> tuple[int, int]:
    return sum(f.correct for f in items), len(items)


def confidence_stats(fields: list[FieldResult]) -> dict[str, Any]:
    """Among values the pipeline actually returned (non-null), do wrong ones score lower?"""
    returned = [f for f in fields if f.predicted is not None and f.confidence is not None]
    right = [f.confidence for f in returned if f.correct]
    wrong = [f.confidence for f in returned if not f.correct]
    nulls_right = [f.confidence for f in fields if f.outcome == "correct_null" and f.confidence is not None]
    missed = [f.confidence for f in fields if f.outcome == "missed" and f.confidence is not None]

    def mean(xs):
        return round(sum(xs) / len(xs), 3) if xs else None

    separation = None
    if right and wrong:  # probability a random correct value outscores a random wrong one (AUROC)
        wins = sum(1.0 if r > w else 0.5 if r == w else 0.0 for r in right for w in wrong)
        separation = round(wins / (len(right) * len(wrong)), 3)

    buckets = []
    for lo, hi in [(0.0, 0.3), (0.3, 0.6), (0.6, 0.8), (0.8, 0.95), (0.95, 1.01)]:
        inside = [f for f in returned if lo <= f.confidence < hi]
        if inside:
            buckets.append({"range": f"{lo:.2f}-{min(hi, 1.0):.2f}", "values": len(inside),
                            "accuracy": round(sum(f.correct for f in inside) / len(inside), 3)})
    return {
        "returned_values": len(returned),
        "mean_confidence_correct": mean(right),
        "mean_confidence_wrong": mean(wrong),
        "correct_count": len(right),
        "wrong_count": len(wrong),
        "separation_auc": separation,
        "mean_confidence_correct_nulls": mean(nulls_right),
        "mean_confidence_missed": mean(missed),
        "buckets": buckets,
        "meaningful": None if mean(wrong) is None else mean(wrong) < mean(right),
    }


def summarize(docs: list[DocumentResult]) -> dict[str, Any]:
    evaluated = [d for d in docs if d.status == "evaluated"]
    fields = [f for d in evaluated for f in d.fields]

    by_type: dict[str, list[FieldResult]] = defaultdict(list)
    by_group: dict[tuple[str, str], list[FieldResult]] = defaultdict(list)
    outcomes: dict[str, int] = defaultdict(int)
    for f in fields:
        by_type[f.document_type].append(f)
        by_group[(f.document_type, f.group)].append(f)
        outcomes[f.outcome] += 1

    current = [d for d in evaluated if not d.predates_validation]
    tp = fp = fn = 0
    for d in current:
        e, a = set(map(tuple, d.validation_expected)), set(map(tuple, d.validation_actual))
        tp, fp, fn = tp + len(e & a), fp + len(a - e), fn + len(e - a)

    return {
        "documents": len(docs),
        "evaluated": len(evaluated),
        "not_run": [d.document for d in docs if d.status == "not_run"],
        "classification": {"correct": sum(d.type_correct for d in evaluated), "total": len(evaluated)},
        "fields": dict(zip(("correct", "total"), _rate(fields))),
        "outcomes": dict(outcomes),
        "by_type": {t: dict(zip(("correct", "total"), _rate(fs))) for t, fs in sorted(by_type.items())},
        "by_field": [
            {"document_type": t, "field": g, "correct": c, "total": n}
            for (t, g), fs in sorted(by_group.items()) for c, n in [_rate(fs)]
        ],
        "validation": {"matched": tp, "unexpected": fp, "missing": fn, "documents_checked": len(current)},
        "predates_validation": [d.document for d in evaluated if d.predates_validation],
        "confidence": confidence_stats([f for d in current for f in d.fields]),
        "confidence_model": confidence_stats([f for d in evaluated for f in (d.model_scores or d.fields)]),
    }


# --- reporting -------------------------------------------------------------------------------------


def pct(c: int, n: int) -> str:
    return f"{c}/{n} ({100 * c / n:.0f}%)" if n else "-"


def document_rows(docs: list[DocumentResult]) -> list[list[str]]:
    rows = []
    for d in docs:
        if d.status == "not_run":
            rows.append([d.document, d.expected_type, "NOT RUN", "-", "-", "-", "-", "-"])
            continue
        c, n = _rate(d.fields)
        bad = defaultdict(int)
        for f in d.fields:
            if not f.correct:
                bad[f.outcome] += 1
        e, a = set(map(tuple, d.validation_expected)), set(map(tuple, d.validation_actual))
        validation = "ok" if e == a else f"missing {len(e - a)}, unexpected {len(a - e)}"
        if d.predates_validation:
            validation = "not checked (predates validation)"
        rows.append([
            d.document, d.expected_type,
            f"{d.predicted_type} {'✓' if d.type_correct else '✗'}",
            pct(c, n), str(bad["hallucinated"]), str(bad["missed"]), str(bad["wrong_value"]), validation,
        ])
    return rows


def table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    line = lambda r: "  ".join(str(x).ljust(w) for x, w in zip(r, widths))  # noqa: E731
    return "\n".join([line(header), line(["-" * w for w in widths]), *map(line, rows)])


def md_table(header: list[str], rows: list[list[str]]) -> str:
    return "\n".join(["| " + " | ".join(header) + " |", "|" + "---|" * len(header),
                      *("| " + " | ".join(str(x).replace("|", "\\|") for x in r) + " |" for r in rows)])


DOC_HEADER = ["Document", "Expected", "Predicted", "Fields correct", "Hallucinated", "Missed", "Wrong", "Validation"]


def render_report(docs: list[DocumentResult], summary: dict[str, Any]) -> str:
    conf, model = summary["confidence"], summary["confidence_model"]
    lines = [
        "# Evaluation against ground truth",
        "",
        f"Documents: {summary['evaluated']} evaluated of {summary['documents']}"
        + (f" (not run: {', '.join(summary['not_run'])})" if summary["not_run"] else ""),
        "",
        *([f"> **{len(summary['predates_validation'])} result(s) predate validation and confidence calibration** "
           f"({', '.join(summary['predates_validation'])}). Their fields are scored; their validation errors and "
           "calibrated confidence are not. Rerun the batch to include them.", ""]
          if summary["predates_validation"] else []),
        "## Headline",
        "",
        f"- Classification: {pct(summary['classification']['correct'], summary['classification']['total'])}",
        f"- Fields: {pct(summary['fields']['correct'], summary['fields']['total'])}"
        f" (hallucinated {summary['outcomes'].get('hallucinated', 0)}, missed {summary['outcomes'].get('missed', 0)},"
        f" wrong value {summary['outcomes'].get('wrong_value', 0)})",
        f"- Validation errors ({summary['validation']['documents_checked']} documents checked):"
        f" {summary['validation']['matched']} matched, {summary['validation']['missing']} missing,"
        f" {summary['validation']['unexpected']} unexpected",
        "",
        "## Per document",
        "",
        md_table(DOC_HEADER, document_rows(docs)),
        "",
        "## Per document type",
        "",
        md_table(["Type", "Fields correct"], [[t, pct(v["correct"], v["total"])] for t, v in summary["by_type"].items()]),
        "",
        "## Per field",
        "",
        md_table(["Type", "Field", "Correct"],
                 [[r["document_type"], f"`{r['field']}`", pct(r["correct"], r["total"])] for r in summary["by_field"]]),
        "",
        "## Does confidence mean something?",
        "",
        "Among values the pipeline returned (non-null). *Gemini's score* is the model's own confidence"
        " (from the trace); *calibrated* is the final confidence after validation, text-layer and"
        " two-reading checks (only results that went through calibration).",
        "",
        md_table(["", "Gemini's score", "Calibrated"], [
            ["Correct values: count", str(model["correct_count"]), str(conf["correct_count"])],
            ["Correct values: mean confidence", str(model["mean_confidence_correct"]), str(conf["mean_confidence_correct"])],
            ["Wrong values (incl. hallucinated): count", str(model["wrong_count"]), str(conf["wrong_count"])],
            ["Wrong values: mean confidence", str(model["mean_confidence_wrong"]), str(conf["mean_confidence_wrong"])],
            ["P(correct value scores above wrong)", str(model["separation_auc"]), str(conf["separation_auc"])],
        ]),
        "",
        *(["With no wrong values there is nothing to separate: confidence can't be judged on this run.", ""]
          if not model["wrong_count"] and not conf["wrong_count"] else []),
        "- P(correct value scores above wrong): the chance a random correct value has higher confidence than a"
        " random wrong one. 0.5 = confidence carries no information, 1.0 = perfect separation.",
        f"- Correct nulls: mean confidence {conf['mean_confidence_correct_nulls']} (low by design: 'not found')",
        f"- Missed values (returned null): mean confidence {conf['mean_confidence_missed']}",
        "",
        md_table(["Confidence", "Values", "Accuracy"], [[b["range"], str(b["values"]), f"{b['accuracy']:.0%}"]
                                                       for b in conf["buckets"]]),
        "",
        "## Every wrong field",
        "",
        md_table(["Document", "Field", "Outcome", "Expected", "Got", "Confidence"], [
            [f.document, f"`{f.field}`", f.outcome, _short(f.expected), _short(f.predicted),
             "-" if f.confidence is None else f"{f.confidence:.2f}"]
            for d in docs for f in d.fields if not f.correct
        ] or [["-", "-", "-", "-", "-", "-"]]),
        "",
        "## Validation errors",
        "",
        md_table(["Document", "Expected", "Actual"], [
            [d.document, ", ".join(f"{c} on {f}" for f, c in d.validation_expected) or "-",
             ", ".join(f"{c} on {f}" for f, c in d.validation_actual) or "-"]
            for d in docs if d.status == "evaluated"
        ]),
        "",
    ]
    return "\n".join(lines)


def _short(v: Any) -> str:
    if isinstance(v, dict) and "printed" in v:
        v = v["printed"]
    text = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return text if len(text) <= 50 else text[:47] + "..."


# --- entry point --------------------------------------------------------------------------------------


def load_truth(samples: Path) -> dict[str, dict[str, Any]]:
    truth = {}
    for folder in [samples / "ground_truth", samples / "negative" / "ground_truth"]:
        for p in sorted(folder.glob("*.json")):
            truth[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    return truth


def evaluate(results_dir: Path, samples_dir: Path) -> tuple[list[DocumentResult], dict[str, Any]]:
    docs = []
    for doc_id, truth in load_truth(samples_dir).items():
        path, trace_path = results_dir / f"{doc_id}.json", results_dir / "raw" / f"{doc_id}.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        trace = json.loads(trace_path.read_text(encoding="utf-8")) if trace_path.is_file() else None
        docs.append(evaluate_document(doc_id, truth, result, trace))
    docs.sort(key=lambda d: (d.expected_type == "unknown", d.document))
    return docs, summarize(docs)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(prog="medextract.evaluate", description="Score results against ground truth")
    parser.add_argument("--results", type=Path, default=root / "results")
    parser.add_argument("--samples", type=Path, default=root / "samples")
    args = parser.parse_args(argv)

    docs, summary = evaluate(args.results, args.samples)
    args.results.mkdir(parents=True, exist_ok=True)
    detail = {"summary": summary, "documents": [
        {**{k: v for k, v in asdict(d).items() if k != "fields"},
         "fields": [asdict(f) | {"correct": f.correct} for f in d.fields]}
        for d in docs
    ]}
    (args.results / "eval.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.results / "eval_report.md").write_text(render_report(docs, summary), encoding="utf-8")

    conf, model = summary["confidence"], summary["confidence_model"]
    print(table(DOC_HEADER, document_rows(docs)))
    print()
    print(table(["Type", "Fields correct"], [[t, pct(v["correct"], v["total"])] for t, v in summary["by_type"].items()]))
    print()
    print(f"classification {pct(summary['classification']['correct'], summary['classification']['total'])} · "
          f"fields {pct(summary['fields']['correct'], summary['fields']['total'])} · "
          f"validation {summary['validation']['matched']} matched / {summary['validation']['missing']} missing / "
          f"{summary['validation']['unexpected']} unexpected "
          f"({summary['validation']['documents_checked']} docs checked)")
    print(table(["Confidence", "Correct: mean (n)", "Wrong: mean (n)", "P(correct > wrong)"], [
        ["Gemini's score", f"{model['mean_confidence_correct']} ({model['correct_count']})",
         f"{model['mean_confidence_wrong']} ({model['wrong_count']})", str(model["separation_auc"])],
        ["calibrated", f"{conf['mean_confidence_correct']} ({conf['correct_count']})",
         f"{conf['mean_confidence_wrong']} ({conf['wrong_count']})", str(conf["separation_auc"])],
    ]))
    if summary["predates_validation"]:
        print(f"predate validation/calibration (rerun the batch): {', '.join(summary['predates_validation'])}")
    if summary["not_run"]:
        print(f"not run: {', '.join(summary['not_run'])}")
    print(f"details: {args.results / 'eval_report.md'}, {args.results / 'eval.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
