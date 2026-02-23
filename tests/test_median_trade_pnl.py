import sqlite3
from src.execution.persistence import init_db, get_median_trade_pnl, DB_PATH


def clear_positions():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def insert_closed(pnl):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO positions
        (token_address, symbol, entry_price, amount, usd_size, status, pnl)
        VALUES (?, ?, ?, ?, ?, 'CLOSED', ?)
    """, ("TEST", "TEST", 0.01, 1000, 10, pnl))
    conn.commit()
    conn.close()


def test_median_odd_count():
    init_db()
    clear_positions()

    insert_closed(50)
    insert_closed(-20)
    insert_closed(30)

    result = get_median_trade_pnl()

    assert result["median_trade_pnl"] == 30.0
    assert result["trade_count"] == 3


def test_median_even_count():
    init_db()
    clear_positions()

    insert_closed(50)
    insert_closed(-20)
    insert_closed(30)
    insert_closed(-10)

    result = get_median_trade_pnl()

    assert result["median_trade_pnl"] == 10.0
    assert result["trade_count"] == 4


def test_median_with_zero():
    init_db()
    clear_positions()

    insert_closed(10)
    insert_closed(0)
    insert_closed(-10)

    result = get_median_trade_pnl()

    assert result["median_trade_pnl"] == 0.0
    assert result["trade_count"] == 3


def test_median_no_trades():
    init_db()
    clear_positions()

    result = get_median_trade_pnl()

    assert result["median_trade_pnl"] == 0.0
    assert result["trade_count"] == 0
