import sqlite3

from src.execution.persistence import DB_PATH
from src.execution.persistence import get_average_trade_pnl
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


def test_average_trade_pnl_mixed_case():
    init_db()
    _clear_positions()

    _insert_closed_trade(50.0)
    _insert_closed_trade(-20.0)
    _insert_closed_trade(30.0)
    _insert_closed_trade(-10.0)

    result = get_average_trade_pnl()

    assert result == {
        "avg_trade_pnl": 12.5,
        "trade_count": 4,
    }


def test_average_trade_pnl_includes_zeros():
    init_db()
    _clear_positions()

    _insert_closed_trade(10.0)
    _insert_closed_trade(0.0)
    _insert_closed_trade(-10.0)

    result = get_average_trade_pnl()

    assert result == {
        "avg_trade_pnl": 0.0,
        "trade_count": 3,
    }


def test_average_trade_pnl_no_trades():
    init_db()
    _clear_positions()

    result = get_average_trade_pnl()

    assert result == {
        "avg_trade_pnl": 0.0,
        "trade_count": 0,
    }


def test_average_trade_pnl_ignores_null_pnl():
    init_db()
    _clear_positions()

    _insert_closed_trade(10.0)
    _insert_closed_trade(None)

    result = get_average_trade_pnl()

    assert result == {
        "avg_trade_pnl": 10.0,
        "trade_count": 1,
    }
