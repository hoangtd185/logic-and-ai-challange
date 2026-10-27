"""Turn our pydantic output models into the JSON Schema sent to Gemini.

Gemini's structured output supports a subset of JSON Schema. This converter
produces only the safe part of that subset:

- ``$ref`` / ``$defs`` are inlined (only self-reference is documented as supported);
- ``anyOf: [X, {"type": "null"}]`` becomes ``"type": [X, "null"]``;
- a ``float | str`` union becomes ``"type": ["number", "string", "null"]``;
- ``title`` and ``default`` are dropped as noise.

The model's reply is then validated by the same pydantic models, so the schema
sent to the LLM can never drift from the output contract.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_KEEP = {"description", "enum", "minimum", "maximum", "additionalProperties", "format"}


def llm_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    return _convert(schema, defs, depth=0)


def _convert(node: dict[str, Any], defs: dict[str, Any], depth: int) -> dict[str, Any]:
    if depth > 20:
        raise ValueError("schema nests too deeply (recursive model?)")

    if "$ref" in node:
        target = defs[node["$ref"].split("/")[-1]]
        merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        return _convert(merged, defs, depth + 1)

    if "anyOf" in node:
        options = [_convert(o, defs, depth + 1) for o in node["anyOf"]]
        simple = all(set(o) <= {"type"} for o in options)
        if simple:  # e.g. string|null, number|string|null
            types: list[str] = []
            for o in options:
                t = o["type"]
                types += t if isinstance(t, list) else [t]
            out: dict[str, Any] = {"type": types if len(types) > 1 else types[0]}
        else:
            non_null = [o for o in options if o.get("type") != "null"]
            if len(non_null) == 1 and len(options) == 2:  # nested object | null
                out = dict(non_null[0])
                t = out.get("type")
                out["type"] = [t, "null"] if isinstance(t, str) else [*t, "null"]
            else:
                out = {"anyOf": options}
        if "description" in node:
            out["description"] = node["description"]
        if "enum" in out and isinstance(out.get("type"), list) and "null" in out["type"]:
            # A nullable enum must list null among its allowed values, or the
            # model can never return null (e.g. a lab flag that is not printed).
            out["enum"] = [*out["enum"], None]
        return out

    out = {k: v for k, v in node.items() if k in _KEEP}
    t = node.get("type")
    if t is not None:
        out["type"] = t
    if t == "integer":
        out["type"] = "number"  # we never need integer-only fields
    if t == "object" or "properties" in node:
        props = node.get("properties", {})
        out["type"] = "object"
        out["properties"] = {k: _convert(v, defs, depth + 1) for k, v in props.items()}
        out["required"] = list(node.get("required", props))
    if t == "array" or "items" in node:
        out["type"] = "array"
        out["items"] = _convert(node.get("items", {}), defs, depth + 1)
    if "enum" in node and "type" not in out:
        out["type"] = "string"
    return out
