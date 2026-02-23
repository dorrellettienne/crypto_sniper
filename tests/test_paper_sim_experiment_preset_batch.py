import csv

from src.runner import paper_sim_experiments


def test_run_seed_sweep_passes_extended_rule_params(monkeypatch):
    captured = []

    def fake_run_simulation(**kwargs):
        captured.append(kwargs)
        return {
            "steps": kwargs["steps"],
            "seed": kwargs["seed"],
            "actions_taken": 1,
            "generated_at_utc": "2026-02-23T00:00:00+00:00",
            "summary": {
                "total_trades": 2,
                "total_pnl": 3.0,
                "wins": 1,
                "losses": 1,
                "win_rate": 50.0,
            },
        }

    monkeypatch.setattr(paper_sim_experiments, "run_simulation", fake_run_simulation)

    paper_sim_experiments.run_seed_sweep(
        steps=10,
        seeds=[7],
        usd_size=100.0,
        stop_loss_percent=0.15,
        sell_price=0.03,
        p_buy=0.3,
        p_stop_loss=0.2,
        p_sell=0.2,
        p_stop_check=0.2,
        p_time_exit=0.1,
    )

    assert len(captured) == 1
    assert captured[0]["usd_size"] == 100.0
    assert captured[0]["stop_loss_percent"] == 0.15
    assert captured[0]["sell_price"] == 0.03
    assert captured[0]["p_buy"] == 0.3
    assert captured[0]["p_time_exit"] == 0.1


def test_run_preset_seed_sweep_batch_builds_aggregate_rows(monkeypatch):
    def fake_run_seed_sweep(steps, seeds, **kwargs):
        if kwargs["usd_size"] == 100.0:
            return [
                {"summary": {"total_pnl": 10.0, "total_trades": 2, "win_rate": 50.0}},
                {"summary": {"total_pnl": -2.0, "total_trades": 4, "win_rate": 25.0}},
            ]
        return [
            {"summary": {"total_pnl": 1.0, "total_trades": 1, "win_rate": 100.0}},
            {"summary": {"total_pnl": 3.0, "total_trades": 3, "win_rate": 50.0}},
        ]

    monkeypatch.setattr(paper_sim_experiments, "run_seed_sweep", fake_run_seed_sweep)

    rows = paper_sim_experiments.run_preset_seed_sweep_batch(
        steps=20,
        seeds=[1, 2],
        presets=[
            {"name": "A", "usd_size": 100.0},
            {"name": "B", "usd_size": 50.0},
        ],
    )

    assert len(rows) == 2
    assert rows[0]["preset_name"] == "A"
    assert rows[0]["seed_count"] == 2
    assert rows[0]["avg_total_pnl"] == 4.0
    assert rows[0]["best_total_pnl"] == 10.0
    assert rows[1]["preset_name"] == "B"
    assert rows[1]["avg_total_trades"] == 2.0


def test_rank_preset_batch_rows_sorts_and_adds_rank():
    ranked = paper_sim_experiments.rank_preset_batch_rows(
        [
            {"preset_name": "beta", "avg_total_pnl": 5.0, "worst_total_pnl": -10.0, "avg_win_rate": 60.0},
            {"preset_name": "alpha", "avg_total_pnl": 5.0, "worst_total_pnl": -5.0, "avg_win_rate": 40.0},
            {"preset_name": "gamma", "avg_total_pnl": 2.0, "worst_total_pnl": -1.0, "avg_win_rate": 90.0},
        ]
    )

    assert [row["preset_name"] for row in ranked] == ["alpha", "beta", "gamma"]
    assert [row["rank"] for row in ranked] == [1, 2, 3]


def test_build_preset_batch_summary_csv_path_uses_csv_extension():
    path = paper_sim_experiments.build_preset_batch_summary_csv_path(
        output_dir="data/exports",
        timestamp_utc="2026-02-23T12:34:56+00:00",
    )
    assert path.endswith(".csv")
    assert "paper_sim_preset_batch_summary_2026-02-23T12-34-56_plus_00-00.csv" in path


def test_save_preset_batch_summary_csv_writes_rows(tmp_path):
    output_path = tmp_path / "preset_batch.csv"
    rows = [
        {
            "preset_name": "balanced",
            "steps": 50,
            "seed_count": 3,
            "usd_size": 50.0,
            "stop_loss_percent": 0.1,
            "sell_price": 0.02,
            "p_buy": 0.2,
            "p_stop_loss": 0.2,
            "p_sell": 0.2,
            "p_stop_check": 0.2,
            "p_time_exit": 0.2,
            "run_count": 3,
            "avg_total_pnl": 5.5,
            "avg_total_trades": 2.0,
            "avg_win_rate": 50.0,
            "best_total_pnl": 10.0,
            "worst_total_pnl": -1.0,
        }
    ]

    written = paper_sim_experiments.save_preset_batch_summary_csv(rows, str(output_path))

    assert written == str(output_path)
    with output_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        loaded = list(reader)

    assert len(loaded) == 1
    assert loaded[0]["preset_name"] == "balanced"
    assert loaded[0]["avg_total_pnl"] == "5.5"
