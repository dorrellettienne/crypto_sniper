import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath("."))

from src.execution.persistence import get_all_time_trade_summary, init_db


DB_PATH = r"data/sniper.db"


def test_all_time_trade_summary():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()

    now_iso = datetime.now().isoformat()

    conn = sqlite3.connect(DB_PATH)
    rows = [
        ("A", "TEST", 0.01, 1000.0, 10.0, "CLOSED", now_iso, 0.02, now_iso, 50.0, 50.0, None),
        ("B", "TEST", 0.01, 1000.0, 10.0, "CLOSED", now_iso, 0.009, now_iso, -20.0, -20.0, None),
        ("C", "TEST", 0.01, 1000.0, 10.0, "CLOSED", now_iso, 0.015, now_iso, 30.0, 30.0, None),
        ("D", "TEST", 0.01, 1000.0, 10.0, "CLOSED", now_iso, 0.009, now_iso, -10.0, -10.0, None),
    ]
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

    summary = get_all_time_trade_summary()

    assert summary["total_trades"] == 4
    assert summary["total_pnl"] == 50.0
    assert summary["wins"] == 2
    assert summary["losses"] == 2
    assert summary["win_rate"] == 50.0
