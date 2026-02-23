import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath("."))

from src.execution.paper_engine import get_last_7_day_trade_summary
from src.execution.persistence import close_position, init_db, insert_position


DB_PATH = r"data/sniper.db"


def reset_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()


def _insert_closed_trade(token_address: str, amount: float, exit_price: float, exit_time_iso: str):
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
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE positions SET exit_time = ? WHERE id = ?",
        (exit_time_iso, position_id),
    )
    conn.commit()
    conn.close()


def test_weekly_summary_excludes_old_trades_and_calculates_metrics():
    reset_db()

    now = datetime.now(timezone.utc)
    today_iso = now.isoformat()
    three_days_ago_iso = (now - timedelta(days=3)).isoformat()
    ten_days_ago_iso = (now - timedelta(days=10)).isoformat()

    # Trade A: +50 today
    _insert_closed_trade("A", amount=50.0, exit_price=2.0, exit_time_iso=today_iso)
    # Trade B: -20 today
    _insert_closed_trade("B", amount=20.0, exit_price=0.0, exit_time_iso=today_iso)
    # Trade C: +30 three days ago
    _insert_closed_trade("C", amount=30.0, exit_price=2.0, exit_time_iso=three_days_ago_iso)
    # Trade D: +100 ten days ago (excluded)
    _insert_closed_trade("D", amount=100.0, exit_price=2.0, exit_time_iso=ten_days_ago_iso)

    summary = get_last_7_day_trade_summary()

    assert summary["total_trades"] == 3
    assert summary["total_pnl"] == 60.0
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert summary["win_rate"] == pytest.approx(66.67, rel=1e-2)
