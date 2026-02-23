import sqlite3

from src.execution.persistence import DB_PATH
from src.execution.persistence import get_downside_deviation
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


def test_downside_deviation_mixed_case():
    init_db()
    clear_positions()

    insert_closed(50.0)
    insert_closed(-20.0)
    insert_closed(30.0)
    insert_closed(-10.0)

    result = get_downside_deviation()

    assert result["loss_count"] == 2
    assert result["downside_deviation"] == 5.0


def test_downside_deviation_only_wins():
    init_db()
    clear_positions()

    insert_closed(10.0)
    insert_closed(20.0)

    result = get_downside_deviation()

    assert result["loss_count"] == 0
    assert result["downside_deviation"] == 0.0


def test_downside_deviation_single_loss():
    init_db()
    clear_positions()

    insert_closed(-50.0)

    result = get_downside_deviation()

    assert result["loss_count"] == 1
    assert result["downside_deviation"] == 0.0


def test_downside_deviation_no_trades():
    init_db()
    clear_positions()

    result = get_downside_deviation()

    assert result["loss_count"] == 0
    assert result["downside_deviation"] == 0.0
