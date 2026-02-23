import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath("."))

import config.settings as settings
from src.execution.paper_engine import simulate_buy, simulate_stop_loss
from src.execution.persistence import (
    get_open_positions,
    get_today_realized_pnl,
    init_db,
)


DB_PATH = r"data/sniper.db"


def reset_positions_table():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def test_max_daily_loss_blocks_new_entries():
    reset_positions_table()

    loss_per_trade = 20.0
    required_losses = int(abs(settings.MAX_DAILY_LOSS) // loss_per_trade) + 1

    for _ in range(required_losses):
        position_id = simulate_buy("TEST123", "TEST", 0.01, 50)
        assert position_id is not None
        simulate_stop_loss(position_id, 0.4)

    daily_pnl = get_today_realized_pnl()
    assert daily_pnl <= settings.MAX_DAILY_LOSS

    open_before = len(get_open_positions())
    blocked = simulate_buy("TEST123", "BLOCKED", 0.01, 50)
    open_after = len(get_open_positions())

    assert blocked is None
    assert open_after == open_before
