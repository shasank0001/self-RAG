from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from statistics import mean
from typing import Any
from uuid import UUID


@dataclass
class IngestionMetricsRegistry:
    counters_by_user: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    stage_durations_ms_by_user: dict[str, dict[str, list[float]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )
    recent_failures_by_user: dict[str, deque[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(lambda: deque(maxlen=200))
    )

    @staticmethod
    def _user_key(user_id: UUID) -> str:
        return str(user_id)

    def incr(self, key: str, *, user_id: UUID, value: int = 1) -> None:
        self.counters_by_user[self._user_key(user_id)][key] += value

    def observe_stage(self, stage: str, duration_ms: float, *, user_id: UUID) -> None:
        self.stage_durations_ms_by_user[self._user_key(user_id)][stage].append(duration_ms)

    def add_failure(self, payload: dict[str, Any], *, user_id: UUID) -> None:
        self.recent_failures_by_user[self._user_key(user_id)].appendleft(payload)

    @staticmethod
    def _format_stage_timers(stage_durations_ms: dict[str, list[float]]) -> dict[str, dict[str, float | int]]:
        return {
            stage: {
                "count": len(values),
                "avg": round(mean(values), 2) if values else 0.0,
                "max": round(max(values), 2) if values else 0.0,
            }
            for stage, values in stage_durations_ms.items()
        }

    def snapshot_for_user(self, user_id: UUID) -> dict[str, Any]:
        user_key = self._user_key(user_id)
        return {
            "counters": dict(self.counters_by_user.get(user_key, Counter())),
            "timers_ms": self._format_stage_timers(self.stage_durations_ms_by_user.get(user_key, {})),
            "recent_failures": list(self.recent_failures_by_user.get(user_key, deque())),
        }

    def snapshot(self) -> dict[str, Any]:
        merged_counters: Counter[str] = Counter()
        merged_stage_durations: dict[str, list[float]] = defaultdict(list)
        merged_failures: deque[dict[str, Any]] = deque(maxlen=200)

        for counters in self.counters_by_user.values():
            merged_counters.update(counters)

        for stage_durations in self.stage_durations_ms_by_user.values():
            for stage, values in stage_durations.items():
                merged_stage_durations[stage].extend(values)

        for failures in self.recent_failures_by_user.values():
            merged_failures.extend(failures)

        return {
            "counters": dict(merged_counters),
            "timers_ms": self._format_stage_timers(merged_stage_durations),
            "recent_failures": list(merged_failures),
        }


_METRICS = IngestionMetricsRegistry()


def get_ingestion_metrics_registry() -> IngestionMetricsRegistry:
    return _METRICS
