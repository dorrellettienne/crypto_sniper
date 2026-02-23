import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.database import init_db, insert_trade  # noqa: E402


if __name__ == "__main__":
    db_path = PROJECT_ROOT / "data" / "sniper.db"
    if db_path.exists():
        db_path.unlink()

    init_db()

    trade_id = insert_trade(
        {
            "token_address": "TEST123",
            "symbol": "TEST",
            "entry_price": 0.01,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "quantity": 1000,
            "status": "OPEN",
        }
    )

    print(f"Inserted trade ID: {trade_id}")
