import csv
import sqlite3

from src.execution.persistence import DB_PATH, init_db
from src.runner.paper_sim_runner import (
    build_closed_trades_export_csv_path,
    save_closed_trades_csv,
)


def _reset_positions():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def _insert_closed_trade(pnl):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO positions
        (token_address, symbol, entry_price, amount, usd_size, status, pnl, exit_price, exit_time)
        VALUES (?, ?, ?, ?, ?, 'CLOSED', ?, ?, datetime('now'))
        """,
        ("TEST", "T", 0.01, 1000, 10, pnl, 0.02),
    )
    conn.commit()
    conn.close()


def test_build_closed_trades_export_csv_path_uses_csv_extension():
    path = build_closed_trades_export_csv_path(
        "data/exports",
        timestamp_utc="2026-02-23T00:00:00+00:00",
    )
    assert path.endswith(".csv")
    assert "paper_sim_closed_trades_" in path


def test_save_closed_trades_csv_writes_rows(tmp_path):
    _reset_positions()
    _insert_closed_trade(10.0)
    _insert_closed_trade(-5.0)

    output_path = tmp_path / "closed_trades.csv"
    written = save_closed_trades_csv(str(output_path))

    assert written == str(output_path)
    with output_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert rows[0]["status"] == "CLOSED"


def test_save_closed_trades_csv_creates_parent_directories(tmp_path):
    _reset_positions()
    output_path = tmp_path / "nested" / "closed_trades.csv"
    save_closed_trades_csv(str(output_path))
    assert output_path.exists()
