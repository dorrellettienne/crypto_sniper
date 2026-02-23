import sqlite3

from src.execution.persistence import (
    init_db,
    get_trade_pnl_coefficient_of_variation,
)

DB_PATH = "data/sniper.db"


def clear_positions():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def insert_closed_trade(pnl):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO positions (
            token_address,
            symbol,
            entry_price,
            amount,
            usd_size,
            status,
            pnl
        )
        VALUES (?, ?, ?, ?, ?, 'CLOSED', ?)
        """,
        ("TEST", "T", 0.01, 1, 1, pnl),
    )
    conn.commit()
    conn.close()


def test_cv_mixed_trades():
    init_db()
    clear_positions()

    for pnl in [50, -20, 30, -10]:
        insert_closed_trade(pnl)

    result = get_trade_pnl_coefficient_of_variation()

    assert result["trade_count"] == 4
    assert result["coefficient_of_variation"] == 2.2891


def test_cv_mean_zero():
    init_db()
    clear_positions()

    insert_closed_trade(10)
    insert_closed_trade(-10)

    result = get_trade_pnl_coefficient_of_variation()

    assert result["trade_count"] == 2
    assert result["coefficient_of_variation"] == 0.0


def test_cv_single_trade():
    init_db()
    clear_positions()

    insert_closed_trade(50)

    result = get_trade_pnl_coefficient_of_variation()

    assert result["trade_count"] == 1
    assert result["coefficient_of_variation"] == 0.0


def test_cv_no_trades():
    init_db()
    clear_positions()

    result = get_trade_pnl_coefficient_of_variation()

    assert result["trade_count"] == 0
    assert result["coefficient_of_variation"] == 0.0
