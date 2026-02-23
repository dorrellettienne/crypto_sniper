from src.runner.paper_sim_runner import format_simulation_summary


def test_format_simulation_summary_preserves_fields():
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

    formatted = format_simulation_summary(result)

    assert formatted == result


def test_format_simulation_summary_does_not_mutate_input():
    result = {
        "steps": 10,
        "seed": 1,
        "actions_taken": 2,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
        "summary": {"total_trades": 1},
    }
    original = {
        "steps": 10,
        "seed": 1,
        "actions_taken": 2,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
        "summary": {"total_trades": 1},
    }

    formatted = format_simulation_summary(result)

    assert result == original
    assert formatted is not result
    assert formatted["summary"] is not result["summary"]


def test_format_simulation_summary_fills_missing_summary_keys():
    result = {
        "steps": 5,
        "seed": 9,
        "actions_taken": 1,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
        "summary": {"total_trades": 2, "wins": 1},
    }

    formatted = format_simulation_summary(result)

    assert formatted["summary"] == {
        "total_trades": 2,
        "total_pnl": 0.0,
        "wins": 1,
        "losses": 0,
        "win_rate": 0.0,
    }


def test_format_simulation_summary_handles_missing_summary():
    result = {
        "steps": 1,
        "seed": 3,
        "actions_taken": 0,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
    }

    formatted = format_simulation_summary(result)

    assert formatted["summary"] == {
        "total_trades": 0,
        "total_pnl": 0.0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
    }
