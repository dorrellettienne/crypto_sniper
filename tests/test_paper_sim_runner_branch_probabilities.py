from src.runner import paper_sim_runner


def _stub_common(monkeypatch):
    monkeypatch.setattr(paper_sim_runner, "init_db", lambda: None)
    monkeypatch.setattr(paper_sim_runner.random, "seed", lambda seed: None)
    monkeypatch.setattr(
        paper_sim_runner,
        "get_today_trade_summary",
        lambda: {"total_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "win_rate": 0.0},
    )


def test_run_simulation_rejects_branch_probabilities_not_summing_to_one():
    try:
        paper_sim_runner.run_simulation(
            steps=1,
            seed=1,
            p_buy=0.5,
            p_stop_loss=0.5,
            p_sell=0.5,
            p_stop_check=0.0,
            p_time_exit=0.0,
        )
    except ValueError as exc:
        assert "sum to 1.0" in str(exc)
    else:
        raise AssertionError("Expected ValueError when branch probabilities do not sum to 1.0")


def test_run_simulation_rejects_negative_branch_probability():
    try:
        paper_sim_runner.run_simulation(
            steps=1,
            seed=1,
            p_buy=-0.1,
            p_stop_loss=0.3,
            p_sell=0.3,
            p_stop_check=0.3,
            p_time_exit=0.2,
        )
    except ValueError as exc:
        assert ">= 0" in str(exc)
    else:
        raise AssertionError("Expected ValueError for negative branch probability")


def test_branch_probability_routes_to_buy(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(paper_sim_runner.random, "random", lambda: 0.05)
    monkeypatch.setattr(paper_sim_runner, "get_open_positions", lambda: [])
    called = {"buy": 0}

    def fake_buy(**kwargs):
        called["buy"] += 1
        return 1

    monkeypatch.setattr(paper_sim_runner, "simulate_buy", fake_buy)

    result = paper_sim_runner.run_simulation(
        steps=1,
        seed=1,
        p_buy=1.0,
        p_stop_loss=0.0,
        p_sell=0.0,
        p_stop_check=0.0,
        p_time_exit=0.0,
    )

    assert called["buy"] == 1
    assert result["actions_taken"] == 1


def test_branch_probability_routes_to_sell(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(paper_sim_runner.random, "random", lambda: 0.45)
    monkeypatch.setattr(paper_sim_runner, "get_open_positions", lambda: [{"id": 1}])
    monkeypatch.setattr(paper_sim_runner, "simulate_sell", lambda position_id, exit_price: 10.0)

    result = paper_sim_runner.run_simulation(
        steps=1,
        seed=1,
        p_buy=0.2,
        p_stop_loss=0.2,
        p_sell=0.2,
        p_stop_check=0.2,
        p_time_exit=0.2,
    )

    assert result["actions_taken"] == 1
