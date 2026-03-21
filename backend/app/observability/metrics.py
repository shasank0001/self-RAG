from __future__ import annotations

import threading
from collections import defaultdict
from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict


def _label_value(value: object) -> str:
    if value in {None, ""}:
        return "unknown"
    return str(value)


class UsageRollupRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    day: date
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    provider_reported_cost_usd: float = 0.0
    estimated_cost_usd: float = 0.0
    fallback_rate: float = 0.0
    fallback_events: int = 0


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple[str, ...], float]] = defaultdict(lambda: defaultdict(float))
        self._histograms: dict[str, dict[tuple[str, ...], list[float]]] = defaultdict(lambda: defaultdict(list))
        self._label_order: dict[str, tuple[str, ...]] = {}

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        with self._lock:
            key, label_values = self._resolve(name=name, labels=labels)
            self._counters[key][label_values] += value

    def observe(self, name: str, value: float, **labels: object) -> None:
        with self._lock:
            key, label_values = self._resolve(name=name, labels=labels)
            self._histograms[key][label_values].append(value)

    def _resolve(self, *, name: str, labels: dict[str, object]) -> tuple[str, tuple[str, ...]]:
        order = tuple(sorted(labels.keys()))
        if name not in self._label_order:
            self._label_order[name] = order
        current_order = self._label_order[name]
        return name, tuple(_label_value(labels.get(label)) for label in current_order)

    def render_prometheus(self) -> str:
        lines: list[str] = []
        buckets = (10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0)

        with self._lock:
            for name, bucket in sorted(self._counters.items()):
                label_names = self._label_order.get(name, ())
                lines.append(f"# TYPE {name} counter")
                for label_values, value in sorted(bucket.items()):
                    suffix = self._format_labels(label_names, label_values)
                    lines.append(f"{name}{suffix} {value}")

            for name, bucket in sorted(self._histograms.items()):
                label_names = self._label_order.get(name, ())
                lines.append(f"# TYPE {name} histogram")
                for label_values, values in sorted(bucket.items()):
                    suffix = self._format_labels(label_names, label_values)
                    count = len(values)
                    total = sum(values)
                    sorted_values = sorted(values)
                    for bucket_value in buckets:
                        bucket_count = 0
                        for observed in sorted_values:
                            if observed <= bucket_value:
                                bucket_count += 1
                            else:
                                break
                        bucket_suffix = self._format_labels((*label_names, "le"), (*label_values, str(bucket_value)))
                        lines.append(f"{name}_bucket{bucket_suffix} {bucket_count}")
                    inf_suffix = self._format_labels((*label_names, "le"), (*label_values, "+Inf"))
                    lines.append(f"{name}_bucket{inf_suffix} {count}")
                    lines.append(f"{name}_count{suffix} {count}")
                    lines.append(f"{name}_sum{suffix} {total}")

        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_labels(label_names: tuple[str, ...], label_values: tuple[str, ...]) -> str:
        if not label_names:
            return ""
        pairs = [f'{name}="{value}"' for name, value in zip(label_names, label_values)]
        return "{" + ",".join(pairs) + "}"


_METRICS = MetricsRegistry()


def get_metrics_registry() -> MetricsRegistry:
    return _METRICS


def get_metrics_router() -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
    async def metrics_endpoint() -> PlainTextResponse:
        payload = get_metrics_registry().render_prometheus()
        return PlainTextResponse(content=payload, media_type="text/plain; version=0.0.4")

    return router


def record_usage_rollup(
    *,
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    provider_reported_cost_usd: float,
    estimated_cost_usd: float,
    fallback: bool,
) -> UsageRollupRecord:
    now = datetime.now(UTC)
    day = date(year=now.year, month=now.month, day=now.day)
    _ = UUID  # keep import used for static analyzers in mixed contexts

    requests = 1
    fallback_events = 1 if fallback else 0
    fallback_rate = 1.0 if fallback else 0.0
    return UsageRollupRecord(
        provider=provider,
        model=model,
        day=day,
        requests=requests,
        tokens_in=max(tokens_in, 0),
        tokens_out=max(tokens_out, 0),
        provider_reported_cost_usd=max(provider_reported_cost_usd, 0.0),
        estimated_cost_usd=max(estimated_cost_usd, 0.0),
        fallback_events=fallback_events,
        fallback_rate=fallback_rate,
    )
