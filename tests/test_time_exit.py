import os
import sqlite3
from datetime import datetime, timedelta

import config.settings as settings
from src.execution.paper_engine import simulate_time_exit
from src.execution.persistence import (
    close_position,
    get_open_positions,
    get_position_by_id,
    init_db,
    insert_position,
)


DB_PATH = r"data/sniper.db"


def reset_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()


def test_time_exit_triggers_for_old_position():
    reset_db()

    position_id = insert_position(
        {
            "token_address": "OLD1",
            "symbol": "TEST",
            "entry_price": 0.01,
            "amount": 1000,
            "usd_size": 10,
        }
    )

    old_created_at = (
        datetime.utcnow() - timedelta(minutes=settings.MAX_POSITION_MINUTES + 10)
    ).isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE positions SET created_at = ? WHERE id = ?",
        (old_created_at, position_id),
    )
    conn.commit()
    conn.close()

    simulate_time_exit(position_id, current_price=0.01)

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT status, pnl FROM positions WHERE id = ?",
        (position_id,),
    ).fetchone()
    conn.close()

    assert row[0] == "CLOSED"
    assert row[1] == 0.0


def test_time_exit_does_not_trigger_for_recent_position():
    reset_db()

    position_id = insert_position(
        {
            "token_address": "NEW1",
            "symbol": "TEST",
            "entry_price": 0.01,
            "amount": 1000,
            "usd_size": 10,
        }
    )

    simulate_time_exit(position_id, current_price=0.01)

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT status FROM positions WHERE id = ?",
        (position_id,),
    ).fetchone()
    conn.close()

    assert row[0] == "OPEN"
