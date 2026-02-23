from src.runner.paper_sim_runner import format_simulation_summary_csv_row


def test_format_simulation_summary_csv_row_flattens_summary():
    result = {
        "steps": 20,
        "seed": 42,
        "actions_taken": 7,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
        "summary": {"total_trades": 3, "total_pnl": 12.5, "wins": 2, "losses": 1, "win_rate": 66.67},
    }

    row = format_simulation_summary_csv_row(result)

    assert row["steps"] == 20
    assert row["seed"] == 42
    assert row["total_trades"] == 3
    assert row["win_rate"] == 66.67


def test_format_simulation_summary_csv_row_fills_defaults():
    row = format_simulation_summary_csv_row({"steps": 1, "seed": 1, "actions_taken": 0, "generated_at_utc": "x"})

    assert row["total_trades"] == 0
    assert row["total_pnl"] == 0.0
    assert row["wins"] == 0
    assert row["losses"] == 0
    assert row["win_rate"] == 0.0


def test_format_simulation_summary_csv_row_does_not_mutate_input():
    result = {"steps": 1, "seed": 1, "actions_taken": 0, "generated_at_utc": "x", "summary": {"wins": 1}}
    original = {"steps": 1, "seed": 1, "actions_taken": 0, "generated_at_utc": "x", "summary": {"wins": 1}}

    _ = format_simulation_summary_csv_row(result)

    assert result == original


def test_format_simulation_summary_csv_row_has_expected_columns():
    row = format_simulation_summary_csv_row({"steps": 1, "seed": 1, "actions_taken": 0, "generated_at_utc": "x"})
    assert list(row.keys()) == [
        "steps",
        "seed",
        "actions_taken",
        "generated_at_utc",
        "total_trades",
        "total_pnl",
        "wins",
        "losses",
        "win_rate",
    ]
