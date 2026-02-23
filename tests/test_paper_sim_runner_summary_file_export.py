import json

from src.runner.paper_sim_runner import save_simulation_summary_json


def _sample_result():
    return {
        "steps": 20,
        "seed": 42,
        "actions_taken": 7,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
        "summary": {
            "total_trades": 3,
            "total_pnl": 12.5,
            "wins": 2,
            "losses": 1,
            "win_rate": 66.67,
        },
    }


def test_save_simulation_summary_json_writes_file(tmp_path):
    output_path = tmp_path / "summary.json"

    written = save_simulation_summary_json(_sample_result(), str(output_path))

    assert written == str(output_path)
    assert output_path.exists()


def test_save_simulation_summary_json_writes_valid_json(tmp_path):
    output_path = tmp_path / "summary.json"
    save_simulation_summary_json(_sample_result(), str(output_path))

    parsed = json.loads(output_path.read_text(encoding="utf-8"))

    assert parsed["steps"] == 20
    assert parsed["summary"]["wins"] == 2


def test_save_simulation_summary_json_overwrites_existing_file(tmp_path):
    output_path = tmp_path / "summary.json"
    output_path.write_text('{"old": true}', encoding="utf-8")

    save_simulation_summary_json(_sample_result(), str(output_path))
    parsed = json.loads(output_path.read_text(encoding="utf-8"))

    assert "old" not in parsed
    assert parsed["seed"] == 42


def test_save_simulation_summary_json_creates_parent_directories(tmp_path):
    output_path = tmp_path / "nested" / "dir" / "summary.json"

    save_simulation_summary_json(_sample_result(), str(output_path))

    assert output_path.exists()
