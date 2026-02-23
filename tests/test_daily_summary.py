import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath("."))

from src.execution.paper_engine import get_today_trade_summary
from src.execution.persistence import init_db


DB_PATH = r"data/sniper.db"


def reset_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()


def test_daily_summary_calculates_metrics_for_today():
    reset_db()

    now_iso = datetime.utcnow().isoformat()

    conn = sqlite3.connect(DB_PATH)
    rows = [
        ("T1", "TEST", 0.01, 1000.0, 10.0, "CLOSED", now_iso, 0.02, now_iso, 50.0, 50.0, None),
        ("T2", "TEST", 0.01, 1000.0, 10.0, "CLOSED", now_iso, 0.009, now_iso, -20.0, -20.0, None),
        ("T3", "TEST", 0.01, 1000.0, 10.0, "CLOSED", now_iso, 0.009, now_iso, -10.0, -10.0, None),
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

    summary = get_today_trade_summary()

    assert isinstance(summary, dict)
    assert summary["total_trades"] == 3
    assert summary["total_pnl"] == 20.0
    assert summary["wins"] == 1
    assert summary["losses"] == 2
    assert round(summary["win_rate"] * 100, 2) == 33.33
