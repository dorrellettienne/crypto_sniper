import sqlite3

import pytest

from config.settings import settings
from src.execution.persistence import DB_PATH, get_open_positions, init_db
from src.runner import paper_sim_runner


def reset_db():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def test_sim_runner_does_not_count_blocked_buys(monkeypatch):
    reset_db()

    monkeypatch.setattr(paper_sim_runner.random, "random", lambda: 0.1)

    result = paper_sim_runner.run_simulation(steps=10, seed=42)

    assert len(get_open_positions()) == settings.max_concurrent_positions
    assert result["actions_taken"] == settings.max_concurrent_positions


def test_sim_runner_same_seed_is_repeatable(monkeypatch):
    monkeypatch.setattr(paper_sim_runner, "init_db", lambda: None)
    monkeypatch.setattr(
        paper_sim_runner,
        "get_open_positions",
        lambda: [{"id": 1}],
    )
    monkeypatch.setattr(
        paper_sim_runner,
        "get_today_trade_summary",
        lambda: {"total_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "win_rate": 0.0},
    )
    monkeypatch.setattr(paper_sim_runner, "simulate_buy", lambda **kwargs: 1)
    monkeypatch.setattr(paper_sim_runner, "simulate_stop_loss", lambda position_id, stop_percent: -1.0)
    monkeypatch.setattr(paper_sim_runner, "simulate_sell", lambda position_id, exit_price: 1.0)
    monkeypatch.setattr(
        paper_sim_runner,
        "simulate_check_stop_trigger",
        lambda position_id, current_price: None,
    )
    monkeypatch.setattr(paper_sim_runner, "simulate_time_exit", lambda position_id, current_price: None)

    first = paper_sim_runner.run_simulation(steps=20, seed=7)
    second = paper_sim_runner.run_simulation(steps=20, seed=7)

    assert first["steps"] == second["steps"] == 20
    assert first["seed"] == second["seed"] == 7
    assert first["actions_taken"] == second["actions_taken"]
    assert first["summary"] == second["summary"]


def test_sim_runner_no_open_position_branches_do_not_execute_actions(monkeypatch):
    values = iter([0.3, 0.5, 0.7, 0.9] * 3)

    monkeypatch.setattr(paper_sim_runner, "init_db", lambda: None)
    monkeypatch.setattr(paper_sim_runner.random, "seed", lambda seed: None)
    monkeypatch.setattr(paper_sim_runner.random, "random", lambda: next(values))
    monkeypatch.setattr(paper_sim_runner, "get_open_positions", lambda: [])
    monkeypatch.setattr(
        paper_sim_runner,
        "get_today_trade_summary",
        lambda: {"total_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "win_rate": 0.0},
    )
    monkeypatch.setattr(
        paper_sim_runner,
        "simulate_stop_loss",
        lambda *args, **kwargs: pytest.fail("simulate_stop_loss should not be called"),
    )
    monkeypatch.setattr(
        paper_sim_runner,
        "simulate_sell",
        lambda *args, **kwargs: pytest.fail("simulate_sell should not be called"),
    )
    monkeypatch.setattr(
        paper_sim_runner,
        "simulate_check_stop_trigger",
        lambda *args, **kwargs: pytest.fail("simulate_check_stop_trigger should not be called"),
    )
    monkeypatch.setattr(
        paper_sim_runner,
        "simulate_time_exit",
        lambda *args, **kwargs: pytest.fail("simulate_time_exit should not be called"),
    )

    result = paper_sim_runner.run_simulation(steps=12, seed=99)

    assert result["steps"] == 12
    assert result["actions_taken"] == 0
    assert result["summary"]["total_trades"] == 0


def test_sim_runner_rejects_negative_steps():
    with pytest.raises(ValueError):
        paper_sim_runner.run_simulation(steps=-1, seed=1)
