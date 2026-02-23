import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath("."))

from src.execution.persistence import get_trade_streaks, init_db


DB_PATH = r"data/sniper.db"


def test_trade_streaks():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()

    start = datetime.now()
    pnls = [50.0, 30.0, -20.0, -10.0, -5.0, 40.0]

    rows = []
    for i, pnl in enumerate(pnls):
        ts = (start + timedelta(minutes=i)).isoformat()
        rows.append(
            (
                f"T{i+1}",
                "TEST",
                0.01,
                1000.0,
                10.0,
                "CLOSED",
                ts,
                0.02,
                ts,
                pnl,
                pnl,
                None,
            )
        )

    conn = sqlite3.connect(DB_PATH)
    conn.executemany(
        """
        INSERT INTO positions (
            token_address,
            symbol,
            entry_price,
            amount,
            usd_size,
            status,
            created_at,
            exit_price,
            exit_time,
            pnl,
            realized_pnl,
            stop_price
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()

    streaks = get_trade_streaks()

    assert streaks["current_win_streak"] == 1
    assert streaks["current_loss_streak"] == 0
    assert streaks["max_win_streak"] == 2
    assert streaks["max_loss_streak"] == 3
