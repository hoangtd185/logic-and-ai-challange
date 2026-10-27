"""Build the mock documents and their ground truth from one source of truth.

    python samples/generator/build.py               # build everything
    python samples/generator/build.py receipt_02    # build one document

Each document is described by one file in ``data/``:

* ``doc.fields`` holds the printed values under the schema's own field names, and
  is exactly what the ground truth contains. ``null`` means "not printed": the
  template omits it and the ground truth expects null.
* ``doc.extra`` holds everything else that is printed (addresses, IDs, distractors)
  and is not part of any schema field.

Outputs:
    samples/documents/<id>.pdf|png             the 10 test documents
    samples/ground_truth/<id>.json             expected extraction per document
    samples/negative/documents|ground_truth/   non-medical documents ("unknown")
    samples/html/<id>.html                     rendered HTML, for inspection
    samples/MANIFEST.md                        table of documents and hard cases

The build fails instead of writing a sample whose ground truth would be wrong:
it checks the schema, that every expected value is actually printed, and that
each planted validation trap (total mismatch, invalid date) really holds.

Needs: pip install -e ".[samples]" && playwright install chromium
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import io
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from PIL import Image, ImageFilter
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "samples" / "generator"
sys.path.insert(0, str(ROOT / "src"))

from medextract.config import TOTAL_MISMATCH_TOLERANCE  # noqa: E402
from medextract.schemas import FIELDS_BY_TYPE, DocumentType, parse_result  # noqa: E402

SAMPLES = ROOT / "samples"
A4_CSS_PX = (794, 1123)  # 210 x 297 mm at 96 dpi
SCAN_SCALE = 2  # render "scans" at 192 dpi before degrading them
GROUND_TRUTH_VERSION = 1
PRINTED_FLAG = {"high": "H", "low": "L", "normal": "N"}  # must match lab_report.html.j2


class SampleError(Exception):
    """The sample's data and its ground truth would disagree."""


# --- formatting helpers shared by templates and checks --------------------------


def money(value: float, style: str) -> str:
    if style == "comma":  # 15,750.00
        return f"{value:,.2f}"
    if style == "comma0":  # 3,010,000 (VND, no decimals)
        return f"{value:,.0f}"
    raise ValueError(f"unknown money style {style!r}")


def qty(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def parse_printed_date(printed: str) -> str | None:
    """DD/MM/YYYY (how every sample prints dates) → ISO, or None if impossible."""
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", printed)
    if not m:
        raise SampleError(f"sample dates must be printed as DD/MM/YYYY, got {printed!r}")
    day, month, year = map(int, m.groups())
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def is_date_field(name: str) -> bool:
    return name == "date" or name.endswith("_date")


# --- ground truth ------------------------------------------------------------------


def build_ground_truth(spec: dict[str, Any], source_file: Path) -> dict[str, Any]:
    doc_type = DocumentType(spec["type"])
    schema_names = list(FIELDS_BY_TYPE[doc_type].model_fields)

    if doc_type is DocumentType.UNKNOWN:
        if spec["doc"]["fields"]:
            raise SampleError("an 'unknown' document must not define schema fields")
        fields: dict[str, Any] = {name: None for name in schema_names}
    else:
        given = spec["doc"]["fields"]
        if list(given) != schema_names:
            raise SampleError(f"fields must be exactly {schema_names}, got {list(given)}")
        fields = {}
        for name, value in given.items():
            if is_date_field(name) and value is not None:
                value = {"printed": value, "iso": parse_printed_date(value)}
            fields[name] = copy.deepcopy(value)

    return {
        "ground_truth_version": GROUND_TRUTH_VERSION,
        "document_id": spec["id"],
        "source_file": source_file.relative_to(SAMPLES).as_posix(),
        "document_type": doc_type.value,
        "fields": fields,
        "absent_fields": absent_paths(fields),
        "expected_validation_errors": spec["expected_validation_errors"],
        "meta": {
            "format": spec["output"]["format"],
            "pages": spec["output"].get("pages", 1),
            "scanned": spec["output"]["format"] == "png",
            "language": spec.get("lang", "en"),
            "currency": spec["doc"]["extra"].get("currency"),
            "hard_cases": spec["hard_cases"],
        },
    }


def absent_paths(fields: dict[str, Any]) -> list[str]:
    """Paths of everything expected to be null: 'payment_method', 'tests[4].flag', ..."""
    paths: list[str] = []
    for name, value in fields.items():
        if value is None:
            paths.append(name)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for i, item in enumerate(value):
                paths += [f"{name}[{i}].{k}" for k, v in item.items() if v is None]
        elif isinstance(value, dict) and not is_date_field(name):
            paths += [f"{name}.{k}" for k, v in value.items() if v is None]
    return paths


def as_extraction_result(gt: dict[str, Any]) -> dict[str, Any]:
    """Wrap the ground truth as a perfect extraction, to check it against the schema."""

    def wrap(value: Any) -> dict[str, Any]:
        if isinstance(value, dict) and "printed" in value:
            value = value["printed"]
        elif isinstance(value, dict):
            value = {k: wrap(v) for k, v in value.items()}
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            value = [{k: wrap(v) for k, v in item.items()} for item in value]
        return {"value": value, "confidence": 0.0 if value is None else 1.0}

    return {
        "document_type": gt["document_type"],
        "confidence": 1.0,
        "fields": {name: wrap(v) for name, v in gt["fields"].items()},
    }


# --- self-checks: the planted traps must really be there ------------------------


def check_sample(spec: dict[str, Any], gt: dict[str, Any], page_text: str) -> None:
    parse_result(as_extraction_result(gt))  # ground truth fits the output schema

    expected = {(e["field"], e["code"]) for e in gt["expected_validation_errors"]}
    fields = gt["fields"]
    found: set[tuple[str, str]] = set()

    for name, value in fields.items():
        if is_date_field(name) and isinstance(value, dict) and value["iso"] is None:
            found.add((name, "INVALID_DATE"))

    if gt["document_type"] == DocumentType.RECEIPT.value:
        items = fields["items"]
        for i, item in enumerate(items):
            if round(item["quantity"] * item["unit_price"], 2) != round(item["total"], 2):
                raise SampleError(f"items[{i}]: quantity x unit_price != total")
        items_sum = sum(item["total"] for item in items)
        gap = abs(items_sum - fields["grand_total"]) / fields["grand_total"]
        if gap > TOTAL_MISMATCH_TOLERANCE:
            found.add(("grand_total", "TOTAL_MISMATCH"))
        print(f"    items sum {items_sum:,.2f} vs grand total {fields['grand_total']:,.2f} ({gap:.1%} gap)")

    if found != expected:
        raise SampleError(f"planted validation errors {sorted(expected)} != actual {sorted(found)}")

    # Every expected string is printed verbatim (whitespace-normalised).
    text = " ".join(page_text.split())
    for path, value in printed_strings(spec):
        if " ".join(value.split()) not in text:
            raise SampleError(f"{path} = {value!r} is not printed in the document")


def printed_strings(spec: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    style = spec["doc"]["extra"].get("money_style")

    def visit(path: str, value: Any, key: str = "") -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for k, v in value.items():
                visit(f"{path}.{k}" if path else k, v, k)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                visit(f"{path}[{i}]", v, key)
        elif key == "flag":
            out.append((path, PRINTED_FLAG[value]))
        elif isinstance(value, str):
            out.append((path, value))
        elif key in {"unit_price", "total", "grand_total"} and style:
            out.append((path, money(value, style)))
        else:
            out.append((path, qty(value)))

    visit("", spec["doc"]["fields"])
    return out


# --- rendering -----------------------------------------------------------------------


def degrade_scan(png: bytes, rotate: float, seed: int) -> Image.Image:
    """Make a clean render look like a phone scan: grey paper, skew, blur, grain, JPEG."""
    img = Image.open(io.BytesIO(png)).convert("L")
    img = img.point(lambda v: int(28 + v * 0.8))  # lower contrast, off-white paper
    img = img.rotate(rotate, resample=Image.BICUBIC, expand=True, fillcolor=96)
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    rng = random.Random(seed)  # seeded: the same scan every build
    noise = Image.frombytes("L", img.size, rng.randbytes(img.width * img.height))
    img = Image.blend(img, noise, 0.10)
    img = img.resize((img.width * 3 // 4, img.height * 3 // 4), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=55)  # compression artefacts
    return Image.open(io.BytesIO(buf.getvalue())).convert("L")


def count_pdf_pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


def build(spec: dict[str, Any], env: Environment, browser) -> dict[str, Any]:
    is_scan = spec["output"]["format"] == "png"
    base = SAMPLES / "negative" if spec.get("negative") else SAMPLES
    doc_path = base / "documents" / f"{spec['id']}.{spec['output']['format']}"
    gt_path = base / "ground_truth" / f"{spec['id']}.json"

    html = env.get_template(spec["template"]).render(
        spec=spec, doc=spec["doc"], lang=spec.get("lang", "en"), scan=is_scan
    )
    gt = build_ground_truth(spec, doc_path)

    page = browser.new_page(
        viewport={"width": A4_CSS_PX[0], "height": A4_CSS_PX[1]}, device_scale_factor=SCAN_SCALE
    )
    try:
        page.set_content(html, wait_until="load")
        check_sample(spec, gt, page.inner_text("body"))
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        if is_scan:
            scan = spec["output"]["scan"]
            img = degrade_scan(page.screenshot(full_page=True), scan["rotate"], scan["seed"])
            img.save(doc_path, "PNG", optimize=True)
        else:
            pdf = page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
            pages = count_pdf_pages(pdf)
            if pages != spec["output"]["pages"]:
                raise SampleError(f"expected {spec['output']['pages']} page(s), rendered {pages}")
            doc_path.write_bytes(pdf)
    finally:
        page.close()

    (SAMPLES / "html").mkdir(exist_ok=True)
    (SAMPLES / "html" / f"{spec['id']}.html").write_text(html, encoding="utf-8")
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    gt_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return gt


def write_manifest(gts: list[dict[str, Any]]) -> None:
    lines = [
        "# Sample documents",
        "",
        "Generated by `samples/generator/build.py` from `samples/generator/data/`. Do not edit by hand.",
        "",
        "| Document | Type | Format | Expected nulls | Expected validation errors | Hard cases |",
        "|---|---|---|---|---|---|",
    ]
    for gt in sorted(gts, key=lambda g: (g["document_type"] == "unknown", g["document_id"])):
        m = gt["meta"]
        fmt = "scanned PNG" if m["scanned"] else f"PDF, {m['pages']} page(s)"
        nulls = ", ".join(f"`{p}`" for p in gt["absent_fields"]) if gt["document_type"] != "unknown" else "all fields"
        errors = ", ".join(f"`{e['code']}` on `{e['field']}`" for e in gt["expected_validation_errors"])
        cases = "<br>".join(m["hard_cases"])
        lines.append(
            f"| [`{gt['document_id']}`]({gt['source_file']}) | {gt['document_type']} | {fmt} "
            f"| {nulls or '-'} | {errors or '-'} | {cases} |"
        )
    (SAMPLES / "MANIFEST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ids", nargs="*", help="document ids to build (default: all)")
    args = parser.parse_args()

    specs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((GENERATOR / "data").glob("*.json"))]
    selected = [s for s in specs if not args.ids or s["id"] in args.ids]
    if args.ids and len(selected) != len(set(args.ids)):
        parser.error(f"unknown id(s): {set(args.ids) - {s['id'] for s in specs}}")

    env = Environment(
        loader=FileSystemLoader(GENERATOR / "templates"),
        undefined=StrictUndefined,
        autoescape=True,
        keep_trailing_newline=True,
    )
    env.globals.update(money=money, qty=qty)

    built, failed = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for spec in selected:
            print(f"- {spec['id']}")
            try:
                built.append(build(spec, env, browser))
            except SampleError as exc:
                failed.append(spec["id"])
                print(f"    FAILED: {exc}")
        browser.close()

    if not args.ids and not failed:
        write_manifest(built)
    print(f"{len(built)} built, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
