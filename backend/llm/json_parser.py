"""Robust JSON extraction from LLM text — no silent field coercion."""

from __future__ import annotations

import json
import re
from typing import Any

from backend.llm.errors import LLMOutputParseError

_FENCE_RE = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE,
)


def extract_json_value(raw_text: str) -> dict[str, Any] | list[Any]:
    """Extract a JSON object/array from model output.

    Supports plain JSON and a single markdown code fence.
    Does not invent values or coerce types.
    """
    text = (raw_text or "").strip()
    if not text:
        raise LLMOutputParseError("empty LLM output")

    candidates: list[str] = [text]
    fences = _FENCE_RE.findall(text)
    candidates.extend(f.strip() for f in fences if f.strip())

    # Try substring from first { or [
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            candidates.append(text[start : end + 1])

    seen: set[str] = set()
    last_err: Exception | None = None
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        try:
            value = json.loads(cand)
        except json.JSONDecodeError as exc:
            last_err = exc
            continue
        if isinstance(value, (dict, list)):
            return value
        raise LLMOutputParseError("LLM JSON root must be object or array")

    raise LLMOutputParseError(
        f"unable to parse JSON from LLM output: {last_err or 'unknown'}"
    )
