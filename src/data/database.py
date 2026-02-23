import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "sniper.db"


def get_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                symbol TEXT,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                exit_price REAL,
                exit_time TEXT,
                quantity REAL NOT NULL,
                status TEXT NOT NULL,
                pnl REAL,
                notes TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_trade(trade_dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(
            """
            INSERT INTO trades (
                token_address,
                symbol,
                entry_price,
                entry_time,
                quantity,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                trade_dict["token_address"],
                trade_dict.get("symbol"),
                trade_dict["entry_price"],
                trade_dict["entry_time"],
                trade_dict["quantity"],
                trade_dict["status"],
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()
