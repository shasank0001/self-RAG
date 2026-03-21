from __future__ import annotations

from pathlib import Path

from app.pipeline.evals.harness import build_offline_graph_result, build_stub_graph_result, run_phase4_evaluation
from app.pipeline.state import RetrievalMode


def test_phase4_eval_harness_produces_report_and_enforces_thresholds(tmp_path: Path) -> None:
    dataset_path = Path(__file__).resolve().parent / "evals" / "golden_phase4_eval_dataset.json"

    report = run_phase4_evaluation(dataset_path, runner=build_offline_graph_result)

    report_path = tmp_path / "phase4_eval_report.json"
    report_path.write_text(report.to_json(), encoding="utf-8")

    assert report.totals["failed"] == 0
    assert report.totals["passed"] == report.totals["total"]
    assert report_path.exists()


def test_phase4_eval_harness_detects_regression_threshold_failures(tmp_path: Path) -> None:
    dataset_path = Path(__file__).resolve().parent / "evals" / "golden_phase4_eval_dataset.json"

    def _failing_runner(case):
        result = build_stub_graph_result(case)
        if case.id == "grounded_001":
            result.state.retrieval_mode = RetrievalMode.PARAMETRIC
            result.state.citations = []
        return result

    report = run_phase4_evaluation(dataset_path, runner=_failing_runner)
    report_path = tmp_path / "phase4_eval_report_fail.json"
    report_path.write_text(report.to_json(), encoding="utf-8")

    assert report.totals["failed"] > 0
    assert report_path.exists()


def test_phase4_eval_harness_stub_runner_still_available_for_unit_paths() -> None:
    dataset_path = Path(__file__).resolve().parent / "evals" / "golden_phase4_eval_dataset.json"
    report = run_phase4_evaluation(dataset_path, runner=build_stub_graph_result)
    assert report.totals["passed"] == report.totals["total"]
