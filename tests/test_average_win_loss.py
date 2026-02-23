import sqlite3

from src.execution.persistence import DB_PATH
from src.execution.persistence import get_average_win_loss
from src.execution.persistence import init_db


def _clear_positions():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def _insert_closed_trade(pnl):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO positions (
            token_address, symbol, entry_price,
            amount, usd_size, status,
            created_at, exit_price, exit_time, pnl
        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, datetime('now'), ?)
        """,
        ("TEST", "T", 0.01, 1000, 10, "CLOSED", 0.02, pnl),
    )
    conn.commit()
    conn.close()


def test_average_win_loss_mixed_trades():
    init_db()
    _clear_positions()

    _insert_closed_trade(50.0)
    _insert_closed_trade(-20.0)
    _insert_closed_trade(30.0)
    _insert_closed_trade(-10.0)

    result = get_average_win_loss()

    assert result["avg_win"] == 40.0
    assert result["avg_loss"] == 15.0
    assert result["win_count"] == 2
    assert result["loss_count"] == 2


def test_average_win_loss_only_wins():
    init_db()
    _clear_positions()

    _insert_closed_trade(10.0)
    _insert_closed_trade(20.0)

    result = get_average_win_loss()

    assert result["avg_win"] == 15.0
    assert result["avg_loss"] == 0.0
    assert result["win_count"] == 2
    assert result["loss_count"] == 0


def test_average_win_loss_only_losses():
    init_db()
    _clear_positions()

    _insert_closed_trade(-10.0)
    _insert_closed_trade(-20.0)

    result = get_average_win_loss()

    assert result["avg_win"] == 0.0
    assert result["avg_loss"] == 15.0
    assert result["win_count"] == 0
    assert result["loss_count"] == 2


def test_average_win_loss_no_trades():
    init_db()
    _clear_positions()

    result = get_average_win_loss()

    assert result["avg_win"] == 0.0
    assert result["avg_loss"] == 0.0
    assert result["win_count"] == 0
    assert result["loss_count"] == 0
