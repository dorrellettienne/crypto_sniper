import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath("."))

from config.settings import settings
from src.execution.paper_engine import simulate_buy, simulate_sell
from src.execution.persistence import get_open_positions, get_position_by_id, init_db


DB_PATH = r"data/sniper.db"


def reset_positions_table():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def test_simulate_buy_and_sell_updates_exact_position():
    reset_positions_table()

    entry_price = 0.01
    usd_size = 50
    expected_amount = 5000.0
    exit_price = 0.02
    expected_pnl = 50.0

    position_id = simulate_buy(
        token_address="TEST123",
        symbol="TEST",
        entry_price=entry_price,
        usd_size=usd_size,
    )

    assert isinstance(position_id, int)

    position = get_position_by_id(position_id)
    assert position is not None
    assert position["status"] == "OPEN"
    assert position["amount"] == expected_amount

    open_positions = get_open_positions()
    assert len(open_positions) == 1
    assert open_positions[0]["id"] == position_id

    pnl = simulate_sell(position_id, exit_price)
    assert pnl == expected_pnl

    closed_position = get_position_by_id(position_id)
    assert closed_position is not None
    assert closed_position["status"] == "CLOSED"
    assert closed_position["exit_price"] == exit_price
    assert closed_position["pnl"] == expected_pnl
