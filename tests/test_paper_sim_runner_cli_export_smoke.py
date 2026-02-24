import json
import sqlite3
import subprocess
import sys

from src.execution.persistence import DB_PATH, init_db


def reset_positions():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def test_paper_sim_runner_cli_export_smoke(tmp_path):
    reset_positions()

    output_path = tmp_path / "sim_summary.json"

    result = subprocess.run(
        [
            sys.executable,
            "src/runner/paper_sim_runner.py",
            "--steps",
            "5",
                "--seed",
                "1",
                "--export-json-path",
                str(output_path),
                "--allow-unsafe-paths",
            ],
        cwd=".",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output_path.exists()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "steps" in payload
    assert "seed" in payload
    assert "actions_taken" in payload
    assert "generated_at_utc" in payload
    assert "summary" in payload
    assert "total_trades" in payload["summary"]
    assert "total_pnl" in payload["summary"]
    assert "wins" in payload["summary"]
    assert "losses" in payload["summary"]
    assert "win_rate" in payload["summary"]
