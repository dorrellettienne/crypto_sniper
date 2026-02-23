import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath("."))

from src.execution.persistence import (
    close_position,
    init_db,
    insert_equity_snapshot,
    insert_position,
)


DB_PATH = r"data/sniper.db"


def _insert_closed_trade(token_address: str, amount: float, exit_price: float):
    position_id = insert_position(
        {
            "token_address": token_address,
            "symbol": "TEST",
            "entry_price": 1.0,
            "amount": amount,
            "usd_size": amount,
        }
    )
    close_position(position_id, exit_price)


def test_insert_equity_snapshot_writes_rows_and_values():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    init_db()

    # PnL = +10
    _insert_closed_trade("A", amount=10.0, exit_price=2.0)
    # PnL = -5
    _insert_closed_trade("B", amount=5.0, exit_price=0.0)

    insert_equity_snapshot(cumulative_pnl=5.0, open_positions=0)

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT cumulative_pnl, open_positions FROM equity_snapshots ORDER BY id ASC"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == 5.0
    assert rows[0][1] == 0

    # Additional PnL = +20, cumulative becomes 25
    _insert_closed_trade("C", amount=20.0, exit_price=2.0)
    insert_equity_snapshot(cumulative_pnl=25.0, open_positions=0)

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT cumulative_pnl, open_positions FROM equity_snapshots ORDER BY id ASC"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[-1][0] == 25.0
    assert rows[-1][1] == 0
