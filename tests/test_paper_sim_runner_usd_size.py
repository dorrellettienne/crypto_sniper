from src.runner import paper_sim_runner


def test_run_simulation_passes_usd_size_to_simulate_buy(monkeypatch):
    captured = []
    values = iter([0.1, 0.9])

    monkeypatch.setattr(paper_sim_runner, "init_db", lambda: None)
    monkeypatch.setattr(paper_sim_runner.random, "seed", lambda seed: None)
    monkeypatch.setattr(paper_sim_runner.random, "random", lambda: next(values))
    monkeypatch.setattr(paper_sim_runner, "get_open_positions", lambda: [])
    monkeypatch.setattr(
        paper_sim_runner,
        "get_today_trade_summary",
        lambda: {"total_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "win_rate": 0.0},
    )

    def fake_buy(**kwargs):
        captured.append(kwargs["usd_size"])
        return 1

    monkeypatch.setattr(paper_sim_runner, "simulate_buy", fake_buy)

    result = paper_sim_runner.run_simulation(steps=2, seed=1, usd_size=100.0)

    assert captured == [100.0]
    assert result["actions_taken"] == 1


def test_run_simulation_rejects_non_positive_usd_size():
    try:
        paper_sim_runner.run_simulation(steps=1, seed=1, usd_size=0)
    except ValueError as exc:
        assert "usd_size" in str(exc)
    else:
        raise AssertionError("Expected ValueError for usd_size <= 0")
