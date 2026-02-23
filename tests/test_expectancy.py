import sqlite3

from src.execution.persistence import DB_PATH
from src.execution.persistence import get_expectancy
from src.execution.persistence import init_db


def clear_positions():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def insert_closed_trade(pnl):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO positions (
            token_address, symbol, entry_price, amount,
            usd_size, status, created_at,
            exit_price, exit_time, pnl
        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, datetime('now'), ?)
        """,
        (
            "TEST",
            "TST",
            0.01,
            1000,
            10,
            "CLOSED",
            0.02,
            pnl,
        ),
    )
    conn.commit()
    conn.close()


def test_expectancy_basic_case():
    init_db()
    clear_positions()

    # +50, -20, +30, -10
    insert_closed_trade(50.0)
    insert_closed_trade(-20.0)
    insert_closed_trade(30.0)
    insert_closed_trade(-10.0)

    result = get_expectancy()

    # wins = [50,30] avg_win = 40
    # losses = [-20,-10] avg_loss = 15
    # win_rate = 2/4 = 0.5
    # expectancy = (0.5*40) - (0.5*15) = 12.5
    assert result["avg_win"] == 40.0
    assert result["avg_loss"] == 15.0
    assert result["win_rate"] == 50.0
    assert result["expectancy"] == 12.5


def test_expectancy_only_wins():
    init_db()
    clear_positions()

    insert_closed_trade(10.0)
    insert_closed_trade(20.0)

    result = get_expectancy()

    assert result["avg_loss"] == 0.0
    assert result["win_rate"] == 100.0
    assert result["expectancy"] == result["avg_win"]


def test_expectancy_no_trades():
    init_db()
    clear_positions()

    result = get_expectancy()

    assert result["expectancy"] == 0.0
    assert result["avg_win"] == 0.0
    assert result["avg_loss"] == 0.0
    assert result["win_rate"] == 0.0
