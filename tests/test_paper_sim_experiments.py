from src.runner import paper_sim_experiments


def test_run_seed_sweep_returns_normalized_runs(monkeypatch):
    def fake_run_simulation(**kwargs):
        return {
            "steps": kwargs["steps"],
            "seed": kwargs["seed"],
            "actions_taken": kwargs["seed"],
            "generated_at_utc": "2026-02-23T00:00:00+00:00",
            "summary": {
                "total_trades": kwargs["seed"],
                "total_pnl": float(kwargs["seed"]),
                "wins": 1,
                "losses": 0,
                "win_rate": 100.0,
            },
        }

    monkeypatch.setattr(paper_sim_experiments, "run_simulation", fake_run_simulation)

    runs = paper_sim_experiments.run_seed_sweep(steps=5, seeds=[1, 2], usd_size=100.0)

    assert len(runs) == 2
    assert runs[0]["steps"] == 5
    assert runs[0]["seed"] == 1
    assert runs[1]["summary"]["total_pnl"] == 2.0


def test_run_seed_sweep_resets_positions_before_each_run(monkeypatch):
    reset_calls = []

    def fake_reset():
        reset_calls.append(True)

    def fake_run_simulation(**kwargs):
        return {
            "steps": kwargs["steps"],
            "seed": kwargs["seed"],
            "actions_taken": 0,
            "generated_at_utc": "2026-02-23T00:00:00+00:00",
            "summary": {"total_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "win_rate": 0.0},
        }

    monkeypatch.setattr(paper_sim_experiments, "_reset_positions_for_experiment_run", fake_reset)
    monkeypatch.setattr(paper_sim_experiments, "run_simulation", fake_run_simulation)

    paper_sim_experiments.run_seed_sweep(steps=5, seeds=[1, 2, 3])

    assert len(reset_calls) == 3


def test_summarize_seed_sweep_aggregates_metrics():
    runs = [
        {"summary": {"total_pnl": 10.0, "total_trades": 2, "win_rate": 50.0}},
        {"summary": {"total_pnl": -5.0, "total_trades": 4, "win_rate": 25.0}},
    ]
    summary = paper_sim_experiments.summarize_seed_sweep(runs)

    assert summary["run_count"] == 2
    assert summary["avg_total_pnl"] == 2.5
    assert summary["avg_total_trades"] == 3.0
    assert summary["avg_win_rate"] == 37.5
    assert summary["best_total_pnl"] == 10.0
    assert summary["worst_total_pnl"] == -5.0


def test_summarize_seed_sweep_empty_returns_zeros():
    summary = paper_sim_experiments.summarize_seed_sweep([])
    assert summary["run_count"] == 0
    assert summary["avg_total_pnl"] == 0.0
    assert summary["best_total_pnl"] == 0.0
