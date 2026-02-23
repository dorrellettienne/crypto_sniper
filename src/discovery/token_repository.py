import sqlite3

from src.data.database import get_connection


def save_token(mint: str, symbol: str, name: str) -> bool:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO discovered_tokens (mint, symbol, name)
            VALUES (?, ?, ?)
            """,
            (mint, symbol, name),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()
