import sqlite3

from config.settings import settings
from src.execution.paper_engine import simulate_buy
from src.execution.persistence import get_open_positions, init_db


DB_PATH = r"data/sniper.db"


def test_max_concurrent_positions_blocks_extra_entry():
    init_db()
    max_positions = settings.max_concurrent_positions

    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()

    position_ids = []
    for _ in range(max_positions):
        position_id = simulate_buy(
            token_address="TEST",
            symbol="TEST",
            entry_price=0.01,
            usd_size=10,
        )
        position_ids.append(position_id)

    assert all(position_id is not None for position_id in position_ids)

    blocked_id = simulate_buy(
        token_address="TEST",
        symbol="TEST",
        entry_price=0.01,
        usd_size=10,
    )

    assert blocked_id is None
    assert len(get_open_positions()) == max_positions
