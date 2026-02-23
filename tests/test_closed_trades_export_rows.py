import sqlite3

from src.execution.persistence import DB_PATH, get_closed_trades_for_export, init_db


def _reset_positions():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def _insert_position(status, pnl=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO positions
        (token_address, symbol, entry_price, amount, usd_size, status, pnl, exit_price, exit_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        ("TEST", "T", 0.01, 1000, 10, status, pnl, 0.02),
    )
    conn.commit()
    conn.close()


def test_get_closed_trades_for_export_returns_only_closed_rows():
    _reset_positions()
    _insert_position("CLOSED", 10.0)
    _insert_position("OPEN", None)
    _insert_position("CLOSED", -5.0)

    rows = get_closed_trades_for_export()

    assert len(rows) == 2
    assert all(row["status"] == "CLOSED" for row in rows)


def test_get_closed_trades_for_export_has_expected_keys():
    _reset_positions()
    _insert_position("CLOSED", 10.0)

    row = get_closed_trades_for_export()[0]

    assert set(row.keys()) >= {
        "id", "token_address", "symbol", "entry_price", "exit_price",
        "amount", "usd_size", "pnl", "status", "created_at", "exit_time",
    }
