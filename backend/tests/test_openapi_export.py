import json
import os
import subprocess
import sys
from pathlib import Path


def test_check_detects_contract_drift_without_rewriting_or_opening_database(tmp_path):
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "schema.json"
    target.write_text("{}")
    environment = {**os.environ, "VM_DATA_DIR": str(tmp_path / "never-initialized")}
    command = [
        sys.executable,
        str(root / "scripts/export-openapi.py"),
        "--check",
        "--output",
        str(target),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=20, env=environment
    )
    assert result.returncode == 1, result.stdout
    assert target.read_text() == "{}"
    assert not (tmp_path / "never-initialized").exists()
    generated = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/export-openapi.py"),
            "--output",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )
    assert generated.returncode == 0, generated.stderr
    assert "/api/v1/jobs/{task_id}/apply" in json.loads(target.read_text())["paths"]
    assert (
        subprocess.run(
            command, capture_output=True, timeout=20, env=environment
        ).returncode
        == 0
    )
