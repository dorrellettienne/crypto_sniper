import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.database import init_db
from src.discovery.token_repository import save_token


if __name__ == "__main__":
    db_path = PROJECT_ROOT / "data" / "sniper.db"
    if db_path.exists():
        db_path.unlink()

    init_db()

    test_mint = f"TEST_MINT_{int(time.time() * 1000)}"
    first = save_token(test_mint, "TEST", "Test Token")
    second = save_token(test_mint, "TEST", "Test Token")

    print(first)
    print(second)
