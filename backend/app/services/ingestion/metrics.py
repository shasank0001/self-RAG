from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from statistics import mean
from typing import Any


@dataclass
class IngestionMetricsRegistry:
    counters: Counter[str] = field(default_factory=Counter)
    stage_durations_ms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    recent_failures: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))

    def incr(self, key: str, value: int = 1) -> None:
        self.counters[key] += value

    def observe_stage(self, stage: str, duration_ms: float) -> None:
        self.stage_durations_ms[stage].append(duration_ms)

    def add_failure(self, payload: dict[str, Any]) -> None:
        self.recent_failures.appendleft(payload)

    def snapshot(self) -> dict[str, Any]:
        return {
            "counters": dict(self.counters),
            "timers_ms": {
                stage: {
                    "count": len(values),
                    "avg": round(mean(values), 2) if values else 0.0,
                    "max": round(max(values), 2) if values else 0.0,
                }
                for stage, values in self.stage_durations_ms.items()
            },
            "recent_failures": list(self.recent_failures),
        }


_METRICS = IngestionMetricsRegistry()


def get_ingestion_metrics_registry() -> IngestionMetricsRegistry:
    return _METRICS
