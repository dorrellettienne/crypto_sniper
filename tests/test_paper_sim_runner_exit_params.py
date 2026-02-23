from src.runner import paper_sim_runner


def test_run_simulation_passes_stop_loss_percent(monkeypatch):
    monkeypatch.setattr(paper_sim_runner, "init_db", lambda: None)
    monkeypatch.setattr(paper_sim_runner.random, "seed", lambda seed: None)
    monkeypatch.setattr(paper_sim_runner.random, "random", lambda: 0.3)
    monkeypatch.setattr(paper_sim_runner, "get_open_positions", lambda: [{"id": 123}])
    monkeypatch.setattr(
        paper_sim_runner,
        "get_today_trade_summary",
        lambda: {"total_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "win_rate": 0.0},
    )

    captured = {}

    def fake_stop_loss(position_id, stop_percent):
        captured["position_id"] = position_id
        captured["stop_percent"] = stop_percent
        return -5.0

    monkeypatch.setattr(paper_sim_runner, "simulate_stop_loss", fake_stop_loss)

    result = paper_sim_runner.run_simulation(
        steps=1,
        seed=1,
        stop_loss_percent=0.25,
    )

    assert captured["position_id"] == 123
    assert captured["stop_percent"] == 0.25
    assert result["actions_taken"] == 1


def test_run_simulation_passes_sell_price(monkeypatch):
    monkeypatch.setattr(paper_sim_runner, "init_db", lambda: None)
    monkeypatch.setattr(paper_sim_runner.random, "seed", lambda seed: None)
    monkeypatch.setattr(paper_sim_runner.random, "random", lambda: 0.5)
    monkeypatch.setattr(paper_sim_runner, "get_open_positions", lambda: [{"id": 456}])
    monkeypatch.setattr(
        paper_sim_runner,
        "get_today_trade_summary",
        lambda: {"total_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "win_rate": 0.0},
    )

    captured = {}

    def fake_sell(position_id, exit_price):
        captured["position_id"] = position_id
        captured["exit_price"] = exit_price
        return 10.0

    monkeypatch.setattr(paper_sim_runner, "simulate_sell", fake_sell)

    result = paper_sim_runner.run_simulation(
        steps=1,
        seed=1,
        sell_price=0.035,
    )

    assert captured["position_id"] == 456
    assert captured["exit_price"] == 0.035
    assert result["actions_taken"] == 1


def test_run_simulation_rejects_non_positive_stop_loss_percent():
    try:
        paper_sim_runner.run_simulation(steps=1, seed=1, stop_loss_percent=0)
    except ValueError as exc:
        assert "stop_loss_percent" in str(exc)
    else:
        raise AssertionError("Expected ValueError for stop_loss_percent <= 0")


def test_run_simulation_rejects_non_positive_sell_price():
    try:
        paper_sim_runner.run_simulation(steps=1, seed=1, sell_price=0)
    except ValueError as exc:
        assert "sell_price" in str(exc)
    else:
        raise AssertionError("Expected ValueError for sell_price <= 0")
