import sqlite3
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "sniper.db"


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT UNIQUE,
                symbol TEXT,
                name TEXT,
                first_seen INTEGER
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def token_exists(mint):
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT 1 FROM tokens WHERE mint = ? LIMIT 1",
            (mint,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def save_token(token_dict):
    mint = token_dict["mint"]
    symbol = token_dict.get("symbol", "")
    name = token_dict.get("name", "")

    if token_exists(mint):
        print(f"Token already exists: {symbol}")
        return False

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            INSERT INTO tokens (mint, symbol, name, first_seen)
            VALUES (?, ?, ?, ?)
            """,
            (mint, symbol, name, int(time.time())),
        )
        conn.commit()
        print(f"Token saved: {symbol}")
        return True
    except sqlite3.IntegrityError:
        print(f"Token already exists: {symbol}")
        return False
    finally:
        conn.close()


def count_tokens():
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()

    test_token = {
        "mint": "abc123",
        "symbol": "TEST",
        "name": "Test Token",
    }

    save_token(test_token)
    save_token(test_token)
    print(f"Total tokens in DB: {count_tokens()}")
