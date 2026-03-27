from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_embedding_router_import_does_not_hit_pipeline_cycle() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(backend_root)

    result = subprocess.run(
        [sys.executable, "-c", "import app.router.embedding_router"],
        cwd=backend_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
