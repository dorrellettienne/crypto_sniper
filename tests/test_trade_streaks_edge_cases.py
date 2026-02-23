import sqlite3
from datetime import datetime, timedelta, timezone

from src.execution.persistence import get_trade_streaks, init_db


DB_PATH = r"data/sniper.db"


def _clear_positions():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def _insert_closed_trades(pnls):
    base = datetime.now(timezone.utc)
    rows = []
    for i, pnl in enumerate(pnls):
        ts = (base + timedelta(minutes=i)).isoformat()
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


def test_trade_streaks_no_closed_trades():
    init_db()
    _clear_positions()

    streaks = get_trade_streaks()

    assert streaks == {
        "current_win_streak": 0,
        "current_loss_streak": 0,
        "max_win_streak": 0,
        "max_loss_streak": 0,
    }


def test_trade_streaks_only_wins():
    init_db()
    _clear_positions()
    _insert_closed_trades([10.0, 20.0, 30.0])

    streaks = get_trade_streaks()

    assert streaks["current_win_streak"] == 3
    assert streaks["current_loss_streak"] == 0
    assert streaks["max_win_streak"] == 3
    assert streaks["max_loss_streak"] == 0


def test_trade_streaks_only_losses():
    init_db()
    _clear_positions()
    _insert_closed_trades([-5.0, -10.0, -15.0, -20.0])

    streaks = get_trade_streaks()

    assert streaks["current_win_streak"] == 0
    assert streaks["current_loss_streak"] == 4
    assert streaks["max_win_streak"] == 0
    assert streaks["max_loss_streak"] == 4


def test_trade_streaks_ignores_zero_pnl():
    init_db()
    _clear_positions()
    _insert_closed_trades([10.0, 0.0, 20.0, -5.0, 0.0, -15.0])

    streaks = get_trade_streaks()

    assert streaks["max_win_streak"] == 2
    assert streaks["max_loss_streak"] == 2
    assert streaks["current_loss_streak"] == 2
    assert streaks["current_win_streak"] == 0
