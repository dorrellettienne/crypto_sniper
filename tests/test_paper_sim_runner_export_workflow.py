import json

from src.runner.paper_sim_runner import (
    build_simulation_summary_export_path,
    save_simulation_summary_json,
)


def test_export_workflow_builds_and_writes_json(tmp_path):
    result = {
        "steps": 5,
        "seed": 1,
        "actions_taken": 0,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
        "summary": {"total_trades": 0},
    }
    path = build_simulation_summary_export_path(
        output_dir=str(tmp_path),
        prefix="run",
        timestamp_utc="2026-02-23T00:00:00+00:00",
    )

    written = save_simulation_summary_json(result, path)
    parsed = json.loads(open(written, "r", encoding="utf-8").read())

    assert written == path
    assert parsed["summary"]["total_trades"] == 0
    assert parsed["summary"]["win_rate"] == 0.0
