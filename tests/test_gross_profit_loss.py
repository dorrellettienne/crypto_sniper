import sqlite3
from src.execution.persistence import init_db, get_gross_profit_loss

DB_PATH = "data/sniper.db"


def clear_positions():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def insert_closed_trade(pnl):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO positions
        (token_address, symbol, entry_price, amount, usd_size, status, pnl)
        VALUES (?, ?, ?, ?, ?, 'CLOSED', ?)
    """,
        ("TEST", "TEST", 0.01, 1000, 10, pnl),
    )
    conn.commit()
    conn.close()


def test_basic_profit_loss_case():
    init_db()
    clear_positions()

    insert_closed_trade(50.0)
    insert_closed_trade(-20.0)
    insert_closed_trade(30.0)
    insert_closed_trade(-10.0)

    result = get_gross_profit_loss()

    assert result["gross_profit"] == 80.0
    assert result["gross_loss"] == 30.0
    assert result["net_pnl"] == 50.0


def test_only_wins():
    init_db()
    clear_positions()

    insert_closed_trade(10.0)
    insert_closed_trade(20.0)

    result = get_gross_profit_loss()

    assert result["gross_profit"] == 30.0
    assert result["gross_loss"] == 0.0
    assert result["net_pnl"] == 30.0


def test_only_losses():
    init_db()
    clear_positions()

    insert_closed_trade(-10.0)
    insert_closed_trade(-20.0)

    result = get_gross_profit_loss()

    assert result["gross_profit"] == 0.0
    assert result["gross_loss"] == 30.0
    assert result["net_pnl"] == -30.0


def test_no_trades():
    init_db()
    clear_positions()

    result = get_gross_profit_loss()

    assert result["gross_profit"] == 0.0
    assert result["gross_loss"] == 0.0
    assert result["net_pnl"] == 0.0
