import sqlite3

from src.execution.persistence import DB_PATH
from src.execution.persistence import get_trade_pnl_std_dev
from src.execution.persistence import init_db


def clear_positions():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def insert_closed(pnl):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO positions
        (token_address, symbol, entry_price, amount, usd_size, status, pnl)
        VALUES (?, ?, ?, ?, ?, 'CLOSED', ?)
        """,
        ("TEST", "TEST", 0.01, 1000, 10, pnl),
    )
    conn.commit()
    conn.close()


def test_trade_pnl_std_dev_basic_mixed_case():
    init_db()
    clear_positions()

    insert_closed(50.0)
    insert_closed(-20.0)
    insert_closed(30.0)
    insert_closed(-10.0)

    result = get_trade_pnl_std_dev()

    assert result["trade_count"] == 4
    assert result["std_dev"] == 28.6138


def test_trade_pnl_std_dev_includes_zero():
    init_db()
    clear_positions()

    insert_closed(10.0)
    insert_closed(0.0)
    insert_closed(-10.0)

    result = get_trade_pnl_std_dev()

    assert result["trade_count"] == 3
    assert result["std_dev"] == 8.165


def test_trade_pnl_std_dev_single_trade():
    init_db()
    clear_positions()

    insert_closed(50.0)

    result = get_trade_pnl_std_dev()

    assert result["trade_count"] == 1
    assert result["std_dev"] == 0.0


def test_trade_pnl_std_dev_no_trades():
    init_db()
    clear_positions()

    result = get_trade_pnl_std_dev()

    assert result["trade_count"] == 0
    assert result["std_dev"] == 0.0
