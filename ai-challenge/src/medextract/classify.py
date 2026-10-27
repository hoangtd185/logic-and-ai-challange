"""Step 1: decide which of the document types this is."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from medextract.ingest import Document
from medextract.llm.client import CallStats, ImagePart, LLMClient, LLMError
from medextract.llm.prompts import load_prompt
from medextract.llm.schema import llm_schema
from medextract.schemas import DocumentType


class ClassificationResponse(BaseModel):
    """What the model returns. ``evidence`` comes first so the model states what it
    sees before it commits to a label."""

    model_config = ConfigDict(extra="forbid")

    evidence: str = Field(description="Document title and 2-3 visible features that decided the type.")
    document_type: DocumentType
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class Classification:
    document_type: DocumentType
    confidence: float
    evidence: str
    raw: dict[str, Any]
    stats: CallStats


CLASSIFY_SCHEMA = llm_schema(ClassificationResponse)


def page_parts(doc: Document) -> list[ImagePart | str]:
    parts: list[ImagePart | str] = []
    for i, page in enumerate(doc.pages, start=1):
        if len(doc.pages) > 1:
            parts.append(f"Page {i} of {len(doc.pages)}:")
        parts.append(ImagePart(page.data, page.mime_type))
    return parts


def classify(doc: Document, client: LLMClient) -> Classification:
    response = client.generate_json(
        system=load_prompt("classify"),
        parts=[*page_parts(doc), "Classify this document."],
        schema=CLASSIFY_SCHEMA,
    )
    try:
        parsed = ClassificationResponse.model_validate(response.data)
    except ValidationError as exc:
        # e.g. a document type outside the schema: a model failure, reported like one
        raise LLMError(f"classification reply does not fit the schema: {response.data!r:.200}") from exc
    return Classification(parsed.document_type, parsed.confidence, parsed.evidence, response.data, response.stats)
