import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "sniper.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                mint TEXT UNIQUE,
                decimals INTEGER,
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_token(symbol: str, mint: str, decimals: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            INSERT INTO tokens (symbol, mint, decimals)
            VALUES (?, ?, ?)
            """,
            (symbol, mint, decimals),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()
