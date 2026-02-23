import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath("."))

from src.execution.paper_engine import get_last_7_day_trade_summary, get_today_trade_summary
from src.execution.persistence import calculate_total_equity, init_db, insert_equity_snapshot


DB_PATH = r"data/sniper.db"


def test_reporting_integration_flow():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()

    now = datetime.utcnow()
    today_iso = now.isoformat()
    three_days_ago_iso = (now - timedelta(days=3)).isoformat()
    eight_days_ago_iso = (now - timedelta(days=8)).isoformat()

    conn = sqlite3.connect(DB_PATH)
    rows = [
        # Trade A: +50.0 (today)
        ("A", "TEST", 0.01, 1000.0, 10.0, "CLOSED", today_iso, 0.02, today_iso, 50.0, 50.0, None),
        # Trade B: -20.0 (today)
        ("B", "TEST", 0.01, 1000.0, 10.0, "CLOSED", today_iso, 0.009, today_iso, -20.0, -20.0, None),
        # Trade C: +30.0 (3 days ago)
        ("C", "TEST", 0.01, 1000.0, 10.0, "CLOSED", three_days_ago_iso, 0.015, three_days_ago_iso, 30.0, 30.0, None),
        # Trade D: -10.0 (8 days ago, excluded from weekly)
        ("D", "TEST", 0.01, 1000.0, 10.0, "CLOSED", eight_days_ago_iso, 0.009, eight_days_ago_iso, -10.0, -10.0, None),
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

    cumulative_pnl = conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM positions WHERE status = 'CLOSED'"
    ).fetchone()[0]
    conn.close()

    insert_equity_snapshot(float(cumulative_pnl), 0)

    conn = sqlite3.connect(DB_PATH)
    snapshot_row = conn.execute(
        "SELECT cumulative_pnl, open_positions FROM equity_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    daily = get_today_trade_summary()
    weekly = get_last_7_day_trade_summary()
    equity = calculate_total_equity(current_price=0.03)

    assert daily["total_trades"] == 2
    assert daily["total_pnl"] == 30.0
    assert daily["wins"] == 1
    assert daily["losses"] == 1

    assert weekly["total_trades"] == 3
    assert weekly["total_pnl"] == 60.0
    assert weekly["wins"] == 2
    assert weekly["losses"] == 1

    assert snapshot_row is not None
    assert snapshot_row[0] == 50.0
    assert snapshot_row[1] == 0

    assert equity["realized_pnl"] == 50.0
    assert equity["unrealized_pnl"] == 0.0
    assert equity["total_equity"] == 50.0
