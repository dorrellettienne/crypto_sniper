import sqlite3
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "sniper.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discovered_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint_address TEXT UNIQUE,
                symbol TEXT,
                name TEXT,
                first_seen TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )

        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(discovered_tokens)").fetchall()
        }
        if "last_seen_at" not in columns:
            conn.execute(
                "ALTER TABLE discovered_tokens ADD COLUMN last_seen_at TEXT"
            )
            conn.execute(
                """
                UPDATE discovered_tokens
                SET last_seen_at = COALESCE(last_seen_at, first_seen, CURRENT_TIMESTAMP)
                """
            )
        conn.commit()


def save_token(mint_address: str, symbol: str, name: str):
    now_utc = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        existed = (
            conn.execute(
                """
                SELECT 1
                FROM discovered_tokens
                WHERE mint_address = ?
                LIMIT 1
                """,
                (mint_address,),
            ).fetchone()
            is not None
        )

        conn.execute(
            """
            INSERT INTO discovered_tokens (mint_address, symbol, name, first_seen, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mint_address)
            DO UPDATE SET
                symbol = excluded.symbol,
                name = excluded.name,
                last_seen_at = excluded.last_seen_at
            """,
            (mint_address, symbol, name, now_utc, now_utc),
        )
        conn.commit()

    if existed:
        print(f"Token re-seen, updated last_seen_at: {symbol} ({mint_address})")
    else:
        print(f"Saved token: {symbol} ({mint_address})")


def count_tokens():
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT COUNT(*) FROM discovered_tokens").fetchone()
        return row[0] if row else 0
