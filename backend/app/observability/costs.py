from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings, get_settings


@dataclass(slots=True)
class PricingEntry:
    provider: str
    model: str
    input_per_1k_tokens_usd: float
    output_per_1k_tokens_usd: float


class CostCalculator:
    def __init__(self, entries: list[PricingEntry]) -> None:
        self._entries = {(entry.provider, entry.model): entry for entry in entries}

    @classmethod
    def from_config(cls, settings: Settings | None = None) -> CostCalculator:
        settings = settings or get_settings()
        path = Path(settings.model_pricing_config)
        if not path.is_absolute():
            path = (Path(__file__).resolve().parents[3] / path).resolve()

        if not path.exists():
            return cls(entries=[])

        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return cls(entries=[])

        items = payload.get("models")
        if not isinstance(items, list):
            return cls(entries=[])

        entries: list[PricingEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(
                    PricingEntry(
                        provider=str(item["provider"]),
                        model=str(item["model"]),
                        input_per_1k_tokens_usd=float(item["input_per_1k_tokens_usd"]),
                        output_per_1k_tokens_usd=float(item["output_per_1k_tokens_usd"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return cls(entries=entries)

    def estimate_cost(
        self,
        *,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
    ) -> float:
        entry = self._entries.get((provider, model))
        if entry is None:
            return 0.0

        in_cost = (max(tokens_in, 0) / 1000) * entry.input_per_1k_tokens_usd
        out_cost = (max(tokens_out, 0) / 1000) * entry.output_per_1k_tokens_usd
        return round(in_cost + out_cost, 8)


def extract_usage_from_payload(payload: dict[str, Any]) -> dict[str, int | float]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}

    normalized: dict[str, int | float] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "estimated_cost_usd", "cost"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            normalized[key] = value
    return normalized


_COST_CALCULATOR: CostCalculator | None = None


def get_cost_calculator(settings: Settings | None = None) -> CostCalculator:
    global _COST_CALCULATOR
    if _COST_CALCULATOR is None:
        _COST_CALCULATOR = CostCalculator.from_config(settings=settings)
    return _COST_CALCULATOR
