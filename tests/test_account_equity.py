import os
import sqlite3
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.abspath("."))

from src.execution.persistence import (
    init_db,
    insert_position,
    close_position,
    calculate_total_equity,
    get_total_realized_pnl,
)

DB_PATH = r"data/sniper.db"


def reset_positions_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def test_equity_no_open_positions():
    init_db()
    reset_positions_table()

    # Insert and immediately close a profitable trade
    position_id = insert_position({
        "token_address": "TEST1",
        "symbol": "TST",
        "entry_price": 1.0,
        "amount": 10.0,
        "usd_size": 10.0
    })

    close_position(position_id, 2.0)  # +10 PnL

    equity = calculate_total_equity(current_price=2.0)

    assert equity["realized_pnl"] == 10.0
    assert equity["unrealized_pnl"] == 0.0
    assert equity["total_equity"] == 10.0
    assert equity["open_positions"] == 0


def test_equity_with_open_position_profit():
    init_db()
    reset_positions_table()

    insert_position({
        "token_address": "TEST2",
        "symbol": "TST",
        "entry_price": 1.0,
        "amount": 10.0,
        "usd_size": 10.0
    })

    equity = calculate_total_equity(current_price=2.0)

    assert equity["realized_pnl"] == 0.0
    assert equity["unrealized_pnl"] == 10.0
    assert equity["total_equity"] == 10.0
    assert equity["open_positions"] == 1


def test_equity_with_open_position_loss():
    init_db()
    reset_positions_table()

    insert_position({
        "token_address": "TEST3",
        "symbol": "TST",
        "entry_price": 1.0,
        "amount": 10.0,
        "usd_size": 10.0
    })

    equity = calculate_total_equity(current_price=0.5)

    assert equity["realized_pnl"] == 0.0
    assert equity["unrealized_pnl"] == -5.0
    assert equity["total_equity"] == -5.0
    assert equity["open_positions"] == 1
