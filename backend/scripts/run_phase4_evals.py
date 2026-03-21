from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.evals.harness import build_offline_graph_result, run_phase4_evaluation


def main() -> int:
    backend_root = Path(__file__).resolve().parents[1]
    dataset_path = backend_root / "tests" / "evals" / "golden_phase4_eval_dataset.json"
    output_path = backend_root / "tests" / "evals" / "phase4_eval_report.json"

    report = run_phase4_evaluation(dataset_path, runner=build_offline_graph_result)
    output_path.write_text(report.to_json(), encoding="utf-8")

    if report.totals["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
