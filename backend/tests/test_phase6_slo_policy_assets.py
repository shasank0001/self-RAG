from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_alert_rules_have_runbook_and_owner_labels() -> None:
    alerts_file = REPO_ROOT / "ops/monitoring/prometheus/alerts_phase6.yml"
    assert alerts_file.exists()

    payload = yaml.safe_load(alerts_file.read_text(encoding="utf-8"))
    groups = payload.get("groups", [])
    assert groups, "Prometheus alert groups must be defined"

    rules = []
    for group in groups:
        rules.extend(group.get("rules", []))

    assert rules, "At least one alert rule is required"
    for rule in rules:
        labels = rule.get("labels", {})
        annotations = rule.get("annotations", {})
        assert labels.get("owner"), f"owner label missing on alert {rule.get('alert')}"
        assert labels.get("team"), f"team label missing on alert {rule.get('alert')}"
        assert annotations.get("runbook"), f"runbook annotation missing on alert {rule.get('alert')}"


def test_slo_policy_contains_targets_and_burn_rate_language() -> None:
    policy_file = REPO_ROOT / "docs/runbooks/slo_and_alert_policy.md"
    assert policy_file.exists()
    text = policy_file.read_text(encoding="utf-8").lower()

    assert "slo targets" in text
    assert "burn-rate" in text or "burn rate" in text
    assert "chat" in text and "ingestion" in text and "fallback" in text


def test_dashboard_definition_contains_required_kpis() -> None:
    dashboard_file = REPO_ROOT / "ops/monitoring/grafana/phase6_observability_dashboard.json"
    assert dashboard_file.exists()
    content = dashboard_file.read_text(encoding="utf-8").lower()
    assert "provider call latency p95" in content
    assert "fallback rate" in content
    assert "ingestion failures" in content
    assert "estimated provider cost" in content
