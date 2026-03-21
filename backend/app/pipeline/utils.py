from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


def _extract_json_object(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Empty model response")

    try:
        loaded = json.loads(stripped)
    except json.JSONDecodeError:
        loaded = None

    if isinstance(loaded, dict):
        return loaded

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("Could not locate JSON object in model response")

    candidate = stripped[start : end + 1]
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("Model response JSON must be an object")
    return parsed


def parse_model_from_text(text: str, model_class: type[ModelT]) -> ModelT:
    payload = _extract_json_object(text)
    try:
        return model_class.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
