"""The evaluator itself: a perfect prediction scores 100%, and every kind of error is
counted as the right kind."""

import copy
import json
from pathlib import Path

import pytest

from medextract.evaluate import confidence_stats, evaluate, evaluate_document, main, values_match

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def load_gt(stem):
    for folder in [SAMPLES / "ground_truth", SAMPLES / "negative" / "ground_truth"]:
        path = folder / f"{stem}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(stem)


def wrap(value, confidence=0.95):
    if isinstance(value, dict) and "printed" in value:
        value = value["printed"]
    elif isinstance(value, dict):
        value = {k: wrap(v, confidence) for k, v in value.items()}
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        value = [{k: wrap(v, confidence) for k, v in row.items()} for row in value]
    return {"value": value, "confidence": 0.0 if value is None else confidence}


def perfect(gt):
    return {
        "document_type": gt["document_type"],
        "confidence": 0.97,
        "fields": {k: wrap(v) for k, v in gt["fields"].items()},
        "validation_errors": [{"field": e["field"], "code": e["code"], "message": "", "severity": "error"}
                              for e in gt["expected_validation_errors"]],
    }


ALL = [p.stem for p in sorted((SAMPLES / "ground_truth").glob("*.json"))] + ["electricity_bill_01"]


@pytest.mark.parametrize("stem", ALL)
def test_perfect_prediction_scores_100(stem):
    gt = load_gt(stem)
    doc = evaluate_document(stem, gt, perfect(gt))
    assert doc.type_correct
    assert all(f.correct for f in doc.fields), [(f.field, f.outcome) for f in doc.fields if not f.correct]
    assert doc.validation_actual == doc.validation_expected


def test_counts_every_row_cell():
    doc = evaluate_document("lab_01", load_gt("lab_01"), perfect(load_gt("lab_01")))
    assert len([f for f in doc.fields if f.group.startswith("tests[]")]) == 8 * 5


def test_dates_compared_as_iso():
    assert values_match({"printed": "12/03/2024", "iso": "2024-03-12"}, "2024-03-12", "date")
    assert values_match({"printed": "12/03/2024", "iso": "2024-03-12"}, "12 March 2024", "date")
    assert not values_match({"printed": "12/03/2024", "iso": "2024-03-12"}, "03/12/2024", "date")
    # an impossible printed date must come back as printed
    assert values_match({"printed": "30/02/2024", "iso": None}, "30/02/2024", "discharge_date")
    assert not values_match({"printed": "30/02/2024", "iso": None}, "2024-03-01", "discharge_date")


def test_text_and_number_matching():
    assert values_match("Mr. Anan Wongsakul", "mr anan  wongsakul", "patient_name")
    assert not values_match("Mr. Anan Wongsakul", "Anan Wongsakul", "patient_name")  # honorific dropped
    assert values_match(3252.0, 3252, "grand_total") and not values_match(3252.0, 3352.0, "grand_total")
    assert values_match(["Chest X-ray", "Bronchoscopy"], ["bronchoscopy", "chest x ray"], "procedures_performed")
    assert values_match("Nguyễn Thị Lan", "Nguyễn Thị Lan", "patient_name")  # composed vs decomposed accents


def test_hallucinated_and_missed():
    gt = load_gt("receipt_02")
    pred = perfect(gt)
    pred["fields"]["payment_method"] = {"value": "Cash", "confidence": 0.9}  # not printed
    pred["fields"]["hospital_name"] = {"value": None, "confidence": 0.1}  # printed but not returned
    doc = evaluate_document("receipt_02", gt, pred)
    outcomes = {f.field: f.outcome for f in doc.fields}
    assert outcomes["payment_method"] == "hallucinated"
    assert outcomes["hospital_name"] == "missed"


def test_rows_are_paired_by_name_not_position():
    gt = load_gt("receipt_01")
    pred = perfect(gt)
    rows = pred["fields"]["items"]["value"]
    rows.reverse()
    doc = evaluate_document("receipt_01", gt, pred)
    assert all(f.correct for f in doc.fields)


def test_invented_and_dropped_rows():
    gt = load_gt("receipt_01")
    pred = perfect(gt)
    rows = pred["fields"]["items"]["value"]
    dropped = rows.pop(0)
    rows.append({k: wrap(v) for k, v in
                 {"description": "Parking fee", "quantity": 1, "unit_price": 50.0, "total": 50.0}.items()})
    doc = evaluate_document("receipt_01", gt, pred)
    bad = [(f.field, f.outcome) for f in doc.fields if not f.correct]
    assert sorted(o for _, o in bad) == ["hallucinated"] * 4 + ["missed"] * 4
    assert dropped["description"]["value"] in json.dumps([f.expected for f in doc.fields if f.outcome == "missed"])


def test_non_medical_doc_must_be_unknown():
    gt = load_gt("electricity_bill_01")
    wrong = {"document_type": "receipt", "confidence": 0.6, "validation_errors": [],
             "fields": {"hospital_name": wrap("Lumen Electric Utility Co."), "patient_name": wrap("Ms. Chalida Prasert"),
                        "date": wrap("01/10/2024"), "items": wrap(None), "grand_total": wrap(1845.32),
                        "payment_method": wrap("Direct debit")}}
    doc = evaluate_document("electricity_bill_01", gt, wrong)
    assert not doc.type_correct
    assert sum(f.outcome == "hallucinated" for f in doc.fields) == 5


def test_validation_compared():
    gt = load_gt("discharge_02")
    pred = perfect(gt)
    pred["validation_errors"] = []
    doc = evaluate_document("discharge_02", gt, pred)
    assert doc.validation_expected == [["discharge_date", "INVALID_DATE"]] and doc.validation_actual == []


def test_confidence_stats_separate_right_from_wrong():
    gt = load_gt("receipt_02")
    pred = perfect(gt)
    pred["fields"]["payment_method"] = {"value": "Cash", "confidence": 0.3}
    pred["fields"]["grand_total"] = {"value": 2495.0, "confidence": 0.5}
    stats = confidence_stats(evaluate_document("receipt_02", gt, pred).fields)
    assert stats["wrong_count"] == 2 and stats["mean_confidence_wrong"] == 0.4
    assert stats["mean_confidence_correct"] == 0.95
    assert stats["separation_auc"] == 1.0 and stats["meaningful"] is True


def test_missing_results_are_reported_not_scored(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    gt = load_gt("lab_01")
    (results / "lab_01.json").write_text(json.dumps(perfect(gt)), encoding="utf-8")
    docs, summary = evaluate(results, SAMPLES)
    assert summary["evaluated"] == 1 and len(summary["not_run"]) == len(ALL) - 1
    assert summary["fields"]["correct"] == summary["fields"]["total"]


def test_cli_writes_report_and_json(tmp_path, capsys):
    results = tmp_path / "results"
    results.mkdir()
    for stem in ALL:
        (results / f"{stem}.json").write_text(json.dumps(perfect(load_gt(stem))), encoding="utf-8")
    assert main(["--results", str(results), "--samples", str(SAMPLES)]) == 0
    out = capsys.readouterr().out
    assert "classification 11/11 (100%)" in out and "electricity_bill_01" in out
    report = (results / "eval_report.md").read_text(encoding="utf-8")
    assert "## Per field" in report and "## Does confidence mean something?" in report
    detail = json.loads((results / "eval.json").read_text(encoding="utf-8"))
    assert detail["summary"]["validation"] == {"matched": 2, "unexpected": 0, "missing": 0, "documents_checked": 11}


def test_results_from_before_validation_are_not_counted_as_misses():
    gt = load_gt("discharge_02")
    pred = perfect(gt)
    pred["validation_errors"] = []  # validation did not exist yet
    old_trace = {"model": "m", "extraction": {"raw": pred["fields"]}}  # no "validation" step
    doc = evaluate_document("discharge_02", gt, pred, old_trace)
    assert doc.predates_validation
    from medextract.evaluate import summarize
    summary = summarize([doc])
    assert summary["validation"] == {"matched": 0, "unexpected": 0, "missing": 0, "documents_checked": 0}
    assert summary["predates_validation"] == ["discharge_02"]
    assert summary["confidence"]["returned_values"] == 0  # calibrated stats exclude it
    assert summary["confidence_model"]["returned_values"] > 0  # model-score stats include it


def test_model_score_vs_calibrated_confidence():
    """Gemini says 0.99 for a wrong value; calibration lowered it to 0.3."""
    gt = load_gt("receipt_02")
    raw = perfect(gt)["fields"]
    raw["grand_total"] = {"value": 2495.0, "confidence": 0.99}  # misread
    final = perfect(gt)
    final["fields"]["grand_total"] = {"value": 2495.0, "confidence": 0.3}
    trace = {"model": "m", "extraction": {"raw": raw}, "validation": []}
    doc = evaluate_document("receipt_02", gt, final, trace)
    from medextract.evaluate import summarize
    summary = summarize([doc])
    assert summary["confidence_model"]["mean_confidence_wrong"] == 0.99
    assert summary["confidence"]["mean_confidence_wrong"] == 0.3
    assert summary["confidence_model"]["separation_auc"] < summary["confidence"]["separation_auc"] == 1.0


def test_malformed_raw_reply_does_not_crash():
    gt = load_gt("lab_01")
    trace = {"model": "m", "extraction": {"raw": {"lab_name": "not a field"}}, "validation": []}
    doc = evaluate_document("lab_01", gt, perfect(gt), trace)
    assert all(f.correct for f in doc.fields)
