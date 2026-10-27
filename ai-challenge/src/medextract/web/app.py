"""FastAPI app: one page and one endpoint.

    python -m medextract serve            # http://127.0.0.1:8000
    uvicorn medextract.web.app:app --reload

GET  /              the upload page (static/index.html)
POST /api/extract   multipart file upload → {"result": <envelope>, "meta": {...}}
GET  /api/health    {"ok": true, "model": ...}
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from medextract.config import MAX_UPLOAD_BYTES, ConfigError, load_settings
from medextract.ingest import IngestError
from medextract.llm.client import LLMClient, LLMError, LLMNetworkError, build_client
from medextract.web.service import analyze

STATIC = Path(__file__).parent / "static"


def default_client() -> LLMClient:
    return build_client(load_settings())


def create_app(client_factory: Callable[[], LLMClient] = default_client) -> FastAPI:
    app = FastAPI(title="Medical Document Extractor", docs_url="/api/docs", redoc_url=None)
    holder: dict[str, LLMClient] = {}

    def client() -> LLMClient:
        # Built on first use, so the page loads (and can show the error) without an API key.
        if "client" not in holder:
            try:
                holder["client"] = client_factory()
            except ConfigError as exc:
                raise HTTPException(500, f"Server is not configured: {exc}") from exc
        return holder["client"]

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "model": client().model}

    @app.post("/api/extract")
    def extract(file: UploadFile = File(...)) -> dict:
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        llm = client()
        try:
            return analyze(data, file.filename or "upload", llm)
        except IngestError as exc:
            raise HTTPException(400, str(exc)) from exc
        except LLMNetworkError as exc:
            raise HTTPException(503, f"Could not reach Gemini (network). Try again shortly. {exc}") from exc
        except LLMError as exc:
            raise HTTPException(502, f"Gemini call failed: {exc}") from exc

    return app


app = create_app()
