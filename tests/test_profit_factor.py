import sqlite3

from src.execution.persistence import DB_PATH
from src.execution.persistence import get_profit_factor
from src.execution.persistence import init_db


def test_profit_factor_basic():
    init_db()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM positions")

    # +50, -20, +30, -10
    trades = [50.0, -20.0, 30.0, -10.0]

    for pnl in trades:
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

    # gross profit = 80
    # gross loss = 30
    # 80 / 30 = 2.6667
    assert get_profit_factor() == 2.6667


def test_profit_factor_only_wins():
    init_db()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM positions")

    cursor.execute(
        """
        INSERT INTO positions (
            token_address, symbol, entry_price,
            amount, usd_size, status,
            created_at, exit_price, exit_time, pnl
        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, datetime('now'), ?)
    """,
        ("TEST", "T", 0.01, 1000, 10, "CLOSED", 0.02, 50.0),
    )

    conn.commit()
    conn.close()

    assert get_profit_factor() == float("inf")


def test_profit_factor_no_trades():
    init_db()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM positions")
    conn.commit()
    conn.close()

    assert get_profit_factor() == 0.0
