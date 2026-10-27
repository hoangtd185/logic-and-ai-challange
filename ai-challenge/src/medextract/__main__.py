"""Command line.

    python -m medextract extract path/to/doc.pdf
    python -m medextract batch samples/documents samples/negative/documents --out results
    python -m medextract batch samples/documents samples/negative/documents --out results --only-failed
    python -m medextract serve                     # upload page at http://127.0.0.1:8000

``batch`` writes, for every document:
    results/<name>.json        the output envelope (the brief's format)
    results/raw/<name>.json    trace: raw model replies, adjustments, call timings and usage
plus results/runs.json (latest status of every document) and results/summary.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from medextract.batch import find_documents, run_batch
from medextract.config import DEFAULT_MAX_CONSECUTIVE_NETWORK_FAILURES, ConfigError, load_settings
from medextract.llm.client import CachingClient, build_client
from medextract.pipeline import process_file


def cmd_extract(args) -> int:
    client = build_client(load_settings())
    out = process_file(args.file, client, progress=lambda line: print(line, file=sys.stderr))
    print(json.dumps(out.result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    if args.trace:
        print(json.dumps(out.trace, ensure_ascii=False, indent=2), file=sys.stderr)
    return 0


def cmd_batch(args) -> int:
    settings = load_settings()
    files = find_documents(args.inputs)
    if not files:
        print("no documents found", file=sys.stderr)
        return 1
    client = build_client(settings)
    print(f"model {settings.model}, timeout {settings.timeout_s:.0f}s, up to {settings.max_retries} retries, "
          f"{len(files)} document(s)")

    report = run_batch(
        files,
        process=lambda path: process_file(path, client, progress=print),
        out_dir=Path(args.out),
        model=settings.model,
        only_failed=args.only_failed,
        max_consecutive_network_failures=args.max_network_failures,
    )

    if isinstance(client, CachingClient):
        print(f"cache: {client.hits} hits, {client.misses} API calls")
    complete = len(report.succeeded) - len(report.partial)
    print(f"{complete} ok, {len(report.partial)} partial, {len(report.failed)} failed"
          + (f", {len(report.not_attempted)} not attempted" if report.not_attempted else "")
          + (f", {len(report.skipped_ok)} already done" if report.skipped_ok else "")
          + f" -> {args.out}/summary.md")
    if report.failed or report.not_attempted or report.partial:
        print(f"rerun the rest with: python -m medextract batch {' '.join(args.inputs)} --out {args.out} --only-failed")
        return 1
    return 0


def cmd_serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print('the web page needs FastAPI and uvicorn: pip install -e .', file=sys.stderr)
        return 2
    print(f"open http://{args.host}:{args.port}")
    uvicorn.run("medextract.web.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="medextract", description="Medical document extraction")
    parser.add_argument("-v", "--verbose", action="store_true", help="log retries and SDK details")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="extract one document and print the JSON result")
    p.add_argument("file")
    p.add_argument("--trace", action="store_true", help="also print the raw trace to stderr")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("batch", help="extract every document in the given folders/files")
    p.add_argument("inputs", nargs="+")
    p.add_argument("--out", default="results")
    p.add_argument("--only-failed", action="store_true",
                   help="run only documents without a successful result from the same model")
    p.add_argument("--max-network-failures", type=int, default=DEFAULT_MAX_CONSECUTIVE_NETWORK_FAILURES,
                   help="stop after this many documents in a row fail with network errors (default %(default)s)")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("serve", help="start the upload web page")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
