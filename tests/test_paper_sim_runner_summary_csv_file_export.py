import csv

from src.runner.paper_sim_runner import save_simulation_summary_csv


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


def test_save_simulation_summary_csv_writes_file(tmp_path):
    output_path = tmp_path / "summary.csv"
    written = save_simulation_summary_csv(_sample_result(), str(output_path))
    assert written == str(output_path)
    assert output_path.exists()


def test_save_simulation_summary_csv_writes_header_and_row(tmp_path):
    output_path = tmp_path / "summary.csv"
    save_simulation_summary_csv(_sample_result(), str(output_path))

    with output_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["seed"] == "42"
    assert rows[0]["total_trades"] == "3"


def test_save_simulation_summary_csv_overwrites_existing_file(tmp_path):
    output_path = tmp_path / "summary.csv"
    output_path.write_text("old,data\n1,2\n", encoding="utf-8")
    save_simulation_summary_csv(_sample_result(), str(output_path))
    text = output_path.read_text(encoding="utf-8")
    assert "old,data" not in text
    assert "generated_at_utc" in text


def test_save_simulation_summary_csv_creates_parent_directories(tmp_path):
    output_path = tmp_path / "nested" / "dir" / "summary.csv"
    save_simulation_summary_csv(_sample_result(), str(output_path))
    assert output_path.exists()
