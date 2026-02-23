import sys
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings as app_settings
from src.execution.persistence import (
    calculate_total_equity,
    close_position,
    get_open_positions,
    get_open_position_count,
    get_position_by_id,
    get_today_realized_pnl,
    get_today_trade_summary,
    init_db,
    insert_equity_snapshot,
    insert_position,
)


def simulate_buy(token_address: str, symbol: str, entry_price: float, usd_size: float):
    daily_pnl = round(get_today_realized_pnl(), 2)
    if daily_pnl <= app_settings.settings.max_daily_loss:
        print("[RISK BLOCKED]")
        print("Max daily loss reached")
        print(f"Daily PnL: {daily_pnl}")
        return None

    open_count = get_open_position_count()
    if open_count >= app_settings.settings.max_concurrent_positions:
        print("[RISK BLOCKED]")
        print("Max concurrent positions reached")
        print(f"Open Positions: {open_count}")
        return None

    amount = usd_size / entry_price

    position = {
        "token_address": token_address,
        "symbol": symbol,
        "entry_price": entry_price,
        "amount": amount,
        "usd_size": usd_size,
    }

    position_id = insert_position(position)

    print(f"[PAPER BUY] {symbol}")
    print(f"Entry Price: {entry_price}")
    print(f"USD Size: {usd_size}")
    print(f"Token Amount: {amount}")
    return position_id


def simulate_sell(position_id: int, exit_price: float):
    pnl = close_position(position_id, exit_price)

    print("[PAPER SELL]")
    print(f"Position ID: {position_id}")
    print(f"Exit Price: {exit_price}")
    print(f"PnL: {pnl}")
    return pnl


def simulate_stop_loss(position_id: int, stop_percent: float):
    """
    Simulates a stop loss by closing the position
    at entry_price * (1 - stop_percent)
    """
    conn = sqlite3.connect(PROJECT_ROOT / "data" / "sniper.db")
    cursor = conn.cursor()
    cursor.execute("SELECT entry_price FROM positions WHERE id = ?", (position_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        print(f"[PAPER STOP LOSS] Position {position_id} not found")
        return

    entry_price = row[0]
    stop_price = round(entry_price * (1 - stop_percent), 12)
    pnl = close_position(position_id, stop_price)
    pnl = round(pnl, 2)

    conn = sqlite3.connect(PROJECT_ROOT / "data" / "sniper.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE positions SET exit_price = ?, pnl = ? WHERE id = ?",
        (stop_price, pnl, position_id),
    )
    conn.commit()
    conn.close()

    print("[PAPER STOP LOSS]")
    print(f"Position ID: {position_id}")
    print(f"Stop Percent: {stop_percent}")
    print(f"Stop Price: {stop_price}")
    print(f"PnL: {pnl}")
    return pnl


def simulate_move_stop_to_breakeven(position_id: int):
    conn = sqlite3.connect(PROJECT_ROOT / "data" / "sniper.db")
    cursor = conn.cursor()
    cursor.execute("SELECT entry_price FROM positions WHERE id = ?", (position_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        print(f"[MOVE STOP TO BREAKEVEN] Position {position_id} not found")
        return

    stop_price = row[0]
    cursor.execute(
        "UPDATE positions SET stop_price = ? WHERE id = ?",
        (stop_price, position_id),
    )
    conn.commit()
    conn.close()

    print("[MOVE STOP TO BREAKEVEN]")
    print(f"Position ID: {position_id}")
    print(f"New Stop Price: {stop_price}")
    return stop_price


def simulate_trailing_stop(position_id: int, current_price: float, trailing_percent: float):
    conn = sqlite3.connect(PROJECT_ROOT / "data" / "sniper.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status, stop_price FROM positions WHERE id = ?",
        (position_id,),
    )
    row = cursor.fetchone()

    if not row:
        conn.close()
        print(f"[TRAILING STOP UPDATE] Position {position_id} not found")
        return

    status, existing_stop_price = row
    new_trailing_stop = round(current_price * (1 - trailing_percent), 12)
    updated = False
    final_stop_price = existing_stop_price

    if status == "OPEN":
        if existing_stop_price is None or new_trailing_stop > existing_stop_price:
            cursor.execute(
                "UPDATE positions SET stop_price = ? WHERE id = ?",
                (new_trailing_stop, position_id),
            )
            conn.commit()
            updated = True
            final_stop_price = new_trailing_stop

    conn.close()

    print("[TRAILING STOP UPDATE]")
    print(f"Position ID: {position_id}")
    print(f"Current Price: {current_price}")
    print(f"Old Stop: {existing_stop_price}")
    print(f"New Stop: {final_stop_price}")
    print(f"Updated: {updated}")
    return final_stop_price


def simulate_check_stop_trigger(position_id: int, current_price: float):
    conn = sqlite3.connect(PROJECT_ROOT / "data" / "sniper.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status, stop_price FROM positions WHERE id = ?",
        (position_id,),
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        print(f"[STOP CHECK] Position {position_id} not found")
        return

    status, stop_price = row

    if status != "OPEN":
        print(f"[STOP CHECK] Position {position_id} is not OPEN (status={status})")
        return

    if stop_price is None:
        print(f"[STOP CHECK] Position {position_id} has no stop_price set")
        return

    if current_price <= stop_price:
        final_pnl = round(close_position(position_id, stop_price), 2)
        print("[STOP TRIGGERED]")
        print(f"Position ID: {position_id}")
        print(f"Stop Price: {stop_price}")
        print(f"Exit Price: {stop_price}")
        print(f"Final PnL: {final_pnl}")
        return final_pnl

    print("[STOP NOT TRIGGERED]")
    print(f"Position ID: {position_id}")
    print(f"Current Price: {current_price}")
    print(f"Stop Price: {stop_price}")
    return None


def simulate_time_exit(position_id: int, current_price: float):
    position = get_position_by_id(position_id)

    if not position:
        print(f"[TIME EXIT] Position {position_id} not found")
        return

    if position["status"] != "OPEN":
        print("[TIME EXIT] Position already closed")
        return

    created_at_raw = str(position["created_at"])
    try:
        created_at = datetime.fromisoformat(created_at_raw)
    except ValueError:
        created_at = datetime.strptime(created_at_raw, "%Y-%m-%d %H:%M:%S")
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    elapsed_minutes = int((datetime.now(timezone.utc) - created_at).total_seconds() // 60)

    if elapsed_minutes >= app_settings.MAX_POSITION_MINUTES:
        final_pnl = round(close_position(position_id, current_price), 2)
        print("[TIME EXIT TRIGGERED]")
        print(f"Position ID: {position_id}")
        print(f"Held Minutes: {elapsed_minutes}")
        print(f"Exit Price: {current_price}")
        print(f"Final PnL: {final_pnl}")
        return final_pnl

    print("[TIME EXIT CHECK]")
    print(f"Position ID: {position_id}")
    print(f"Held Minutes: {elapsed_minutes}")
    print("Not triggered")
    return None


def simulate_take_profit_half(position_id: int):
    conn = sqlite3.connect(PROJECT_ROOT / "data" / "sniper.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT entry_price, amount, usd_size, realized_pnl FROM positions WHERE id = ?",
        (position_id,),
    )
    row = cursor.fetchone()

    if not row:
        conn.close()
        print(f"[TAKE PROFIT 50%] Position {position_id} not found")
        return

    entry_price, amount, usd_size, realized_pnl = row
    take_profit_price = round(entry_price * 2, 12)
    sell_amount = round(amount * 0.5, 12)
    remaining_amount = round(amount * 0.5, 12)
    pnl = round((take_profit_price - entry_price) * sell_amount, 2)
    updated_realized_pnl = round((realized_pnl or 0) + pnl, 2)

    cursor.execute(
        "UPDATE positions SET amount = ?, realized_pnl = ?, status = ? WHERE id = ?",
        (remaining_amount, updated_realized_pnl, "OPEN", position_id),
    )
    conn.commit()
    conn.close()

    print("[TAKE PROFIT 50%]")
    print(f"Position ID: {position_id}")
    print(f"Take Profit Price: {take_profit_price}")
    print(f"Sold Amount: {sell_amount}")
    print(f"Realized PnL: {pnl}")
    print(f"Remaining Amount: {remaining_amount}")
    simulate_move_stop_to_breakeven(position_id)
    return pnl


def get_last_7_day_trade_summary():
    conn = sqlite3.connect(PROJECT_ROOT / "data" / "sniper.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            COUNT(*) AS total_trades,
            SUM(pnl) AS total_pnl,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) AS losses
        FROM positions
        WHERE status = 'CLOSED'
          AND DATE(exit_time) >= DATE('now', '-6 days')
        """
    )
    row = cursor.fetchone()
    conn.close()

    total_trades = int(row[0]) if row and row[0] is not None else 0
    total_pnl = float(row[1]) if row and row[1] is not None else 0.0
    wins = int(row[2]) if row and row[2] is not None else 0
    losses = int(row[3]) if row and row[3] is not None else 0
    win_rate = ((wins / total_trades) * 100) if total_trades > 0 else 0

    print("\n=== WEEKLY SUMMARY ===")
    print(f"Total Trades: {total_trades}")
    print(f"Total PnL: {total_pnl}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Win Rate: {round(win_rate, 2)}%")
    return {
        "total_trades": total_trades,
        "total_pnl": total_pnl,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 2),
    }


if __name__ == "__main__":
    init_db()

    entry_price = 0.01
    conn = sqlite3.connect(PROJECT_ROOT / "data" / "sniper.db")
    cursor = conn.cursor()
    yesterday = "2000-01-01T00:00:00+00:00"
    cursor.execute(
        "UPDATE positions SET exit_time = ? WHERE status = 'CLOSED' AND exit_time IS NOT NULL",
        (yesterday,),
    )
    cursor.execute(
        "UPDATE positions SET status = 'CLOSED', pnl = COALESCE(pnl, 0), exit_time = ? WHERE status = 'OPEN'",
        (yesterday,),
    )
    conn.commit()
    conn.close()

    for _ in range(3):
        position_id = simulate_buy(
            token_address="TEST123",
            symbol="TEST",
            entry_price=entry_price,
            usd_size=50,
        )
        if position_id is not None:
            simulate_stop_loss(position_id, 0.40)

    simulate_buy(
        token_address="TEST123",
        symbol="TEST",
        entry_price=entry_price,
        usd_size=50,
    )

    summary = get_today_trade_summary()

    print("\n=== DAILY SUMMARY ===")
    print(f"Total Trades: {summary['total_trades']}")
    print(f"Total PnL: {summary['total_pnl']}")
    print(f"Wins: {summary['wins']}")
    print(f"Losses: {summary['losses']}")
    print(f"Win Rate: {round(summary['win_rate'] * 100, 2)}%")

    get_last_7_day_trade_summary()

    conn = sqlite3.connect(PROJECT_ROOT / "data" / "sniper.db")
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(pnl) FROM positions WHERE status='CLOSED'")
    row = cursor.fetchone()
    conn.close()
    cumulative_pnl = float(row[0]) if row and row[0] is not None else 0.0
    open_count = get_open_position_count()
    insert_equity_snapshot(cumulative_pnl, open_count)

    print("\n=== EQUITY SNAPSHOT SAVED ===")
    print(f"Cumulative PnL: {cumulative_pnl}")
    print(f"Open Positions: {open_count}")

    print("\n=== ACCOUNT EQUITY ===")
    equity = calculate_total_equity(current_price=0.03)
    print(f"Realized PnL: {equity['realized_pnl']}")
    print(f"Unrealized PnL: {equity['unrealized_pnl']}")
    print(f"Total Equity: {equity['total_equity']}")
    print(f"Open Positions: {equity['open_positions']}")
