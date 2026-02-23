import json

from src.runner.paper_sim_runner import format_simulation_summary_json


def test_format_simulation_summary_json_returns_valid_json():
    result = {
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

    json_str = format_simulation_summary_json(result)
    parsed = json.loads(json_str)

    assert parsed["steps"] == 20
    assert parsed["summary"]["total_pnl"] == 12.5


def test_format_simulation_summary_json_does_not_mutate_input():
    result = {
        "steps": 1,
        "seed": 2,
        "actions_taken": 0,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
        "summary": {"wins": 1},
    }
    original = {
        "steps": 1,
        "seed": 2,
        "actions_taken": 0,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
        "summary": {"wins": 1},
    }

    _ = format_simulation_summary_json(result)

    assert result == original


def test_format_simulation_summary_json_fills_missing_summary_defaults():
    result = {
        "steps": 5,
        "seed": 9,
        "actions_taken": 1,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
    }

    parsed = json.loads(format_simulation_summary_json(result))

    assert parsed["summary"] == {
        "total_trades": 0,
        "total_pnl": 0.0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
    }


def test_format_simulation_summary_json_is_deterministic_for_same_input():
    result = {
        "steps": 10,
        "seed": 7,
        "actions_taken": 3,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
        "summary": {"losses": 1, "wins": 2, "total_trades": 3, "win_rate": 66.67, "total_pnl": 10.0},
    }

    first = format_simulation_summary_json(result)
    second = format_simulation_summary_json(result)

    assert first == second
