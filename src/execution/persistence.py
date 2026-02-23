import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "sniper.db"


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                symbol TEXT,
                entry_price REAL NOT NULL,
                amount REAL NOT NULL,
                usd_size REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()
        }
        if "exit_price" not in columns:
            conn.execute("ALTER TABLE positions ADD COLUMN exit_price REAL")
        if "exit_time" not in columns:
            conn.execute("ALTER TABLE positions ADD COLUMN exit_time TEXT")
        if "pnl" not in columns:
            conn.execute("ALTER TABLE positions ADD COLUMN pnl REAL")
        if "realized_pnl" not in columns:
            conn.execute("ALTER TABLE positions ADD COLUMN realized_pnl REAL DEFAULT 0")
        try:
            conn.execute("ALTER TABLE positions ADD COLUMN stop_price REAL")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                cumulative_pnl REAL,
                open_positions INTEGER
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_position(position_dict: dict[str, Any]) -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(
            """
            INSERT INTO positions (
                token_address,
                symbol,
                entry_price,
                amount,
                usd_size,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                position_dict["token_address"],
                position_dict.get("symbol"),
                position_dict["entry_price"],
                position_dict["amount"],
                position_dict["usd_size"],
                "OPEN",
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_open_positions() -> list[dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                id,
                token_address,
                symbol,
                entry_price,
                amount,
                usd_size,
                status,
                created_at
            FROM positions
            WHERE status = ?
            ORDER BY id ASC
            """,
            ("OPEN",),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_position_by_id(position_id: int) -> dict[str, Any] | None:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM positions WHERE id = ?",
            (position_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_open_position_count() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE status = ?",
            ("OPEN",),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def get_today_realized_pnl() -> float:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT SUM(pnl)
            FROM positions
            WHERE status = 'CLOSED'
              AND DATE(exit_time) = DATE('now')
            """
        ).fetchone()
        if not row or row[0] is None:
            return 0.0
        return float(row[0])
    finally:
        conn.close()


def get_today_trade_summary() -> dict[str, float | int]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_trades,
                SUM(pnl) AS total_pnl,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS win_count,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS loss_count
            FROM positions
            WHERE status = 'CLOSED'
              AND DATE(exit_time) = DATE('now')
            """
        ).fetchone()

        total_trades = int(row[0]) if row and row[0] is not None else 0
        total_pnl = float(row[1]) if row and row[1] is not None else 0.0
        wins = int(row[2]) if row and row[2] is not None else 0
        losses = int(row[3]) if row and row[3] is not None else 0
        win_rate = (wins / total_trades) if total_trades > 0 else 0.0

        return {
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
        }
    finally:
        conn.close()


def insert_equity_snapshot(cumulative_pnl: float, open_positions: int):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute(
            """
            INSERT INTO equity_snapshots (
                timestamp,
                cumulative_pnl,
                open_positions
            )
            VALUES (?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                cumulative_pnl,
                open_positions,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def close_position(position_id: int, exit_price: float) -> float:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, entry_price, amount, status
            FROM positions
            WHERE id = ?
            """,
            (position_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Position not found: {position_id}")

        pnl = (exit_price - float(row["entry_price"])) * float(row["amount"])
        exit_time = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """
            UPDATE positions
            SET exit_price = ?,
                exit_time = ?,
                pnl = ?,
                status = ?
            WHERE id = ?
            """,
            (exit_price, exit_time, pnl, "CLOSED", position_id),
        )
        conn.commit()
        return pnl
    finally:
        conn.close()


def get_total_realized_pnl():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COALESCE(SUM(pnl), 0)
        FROM positions
        WHERE status = 'CLOSED'
        """
    )

    result = cursor.fetchone()[0]
    conn.close()
    return result or 0.0


def get_open_positions_full():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, entry_price, amount, realized_pnl
        FROM positions
        WHERE status = 'OPEN'
        """
    )

    rows = cursor.fetchall()
    conn.close()

    positions = []
    for row in rows:
        positions.append(
            {
                "id": row[0],
                "entry_price": row[1],
                "amount": row[2],
                "realized_pnl": row[3] or 0.0,
            }
        )

    return positions


def calculate_total_equity(current_price):
    """
    current_price is manually passed (paper mode simulation).
    """
    realized = get_total_realized_pnl()
    open_positions = get_open_positions_full()

    unrealized = 0.0

    for pos in open_positions:
        entry = pos["entry_price"]
        amount = pos["amount"]

        unrealized += (current_price - entry) * amount

    total_equity = realized + unrealized

    return {
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_equity": total_equity,
        "open_positions": len(open_positions),
    }


def get_all_time_trade_summary() -> dict[str, float | int]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_trades,
                SUM(pnl) AS total_pnl,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS win_count,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS loss_count
            FROM positions
            WHERE status = 'CLOSED'
            """
        ).fetchone()

        total_trades = int(row[0]) if row and row[0] is not None else 0
        total_pnl = float(row[1]) if row and row[1] is not None else 0.0
        wins = int(row[2]) if row and row[2] is not None else 0
        losses = int(row[3]) if row and row[3] is not None else 0
        win_rate = round(((wins / total_trades) * 100), 2) if total_trades > 0 else 0.0

        return {
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
        }
    finally:
        conn.close()


def get_trade_streaks() -> dict[str, int]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT pnl
            FROM positions
            WHERE status = 'CLOSED'
            ORDER BY exit_time ASC, id ASC
            """
        ).fetchall()

        current_win_streak = 0
        current_loss_streak = 0
        max_win_streak = 0
        max_loss_streak = 0

        for row in rows:
            pnl = row[0]
            if pnl is None or pnl == 0:
                continue

            if pnl > 0:
                current_win_streak += 1
                current_loss_streak = 0
                if current_win_streak > max_win_streak:
                    max_win_streak = current_win_streak
            else:
                current_loss_streak += 1
                current_win_streak = 0
                if current_loss_streak > max_loss_streak:
                    max_loss_streak = current_loss_streak

        return {
            "current_win_streak": current_win_streak,
            "current_loss_streak": current_loss_streak,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
        }
    finally:
        conn.close()


def get_profit_factor():
    """
    Calculates profit factor using CLOSED trades only.
    Profit Factor = Gross Profit / Gross Loss
    """

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl FROM positions
        WHERE status = 'CLOSED'
        """
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return 0.0

    gross_profit = 0.0
    gross_loss = 0.0

    for (pnl,) in rows:
        if pnl is None:
            continue
        if pnl > 0:
            gross_profit += pnl
        elif pnl < 0:
            gross_loss += abs(pnl)

    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0

    return round(gross_profit / gross_loss, 4)


def get_expectancy():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl FROM positions
        WHERE status = 'CLOSED'
    """
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "win_rate": 0.0,
        }

    wins = []
    losses = []

    for (pnl,) in rows:
        if pnl is None:
            continue
        if pnl > 0:
            wins.append(pnl)
        elif pnl < 0:
            losses.append(pnl)

    total_trades = len(wins) + len(losses)

    if total_trades == 0:
        return {
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "win_rate": 0.0,
        }

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0

    win_rate = len(wins) / total_trades
    loss_rate = len(losses) / total_trades

    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    return {
        "expectancy": round(expectancy, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "win_rate": round(win_rate * 100, 2),
    }


def get_payoff_ratio():
    """
    Returns payoff ratio = avg_win / avg_loss (absolute loss value)
    Based on CLOSED trades only.
    """

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl FROM positions
        WHERE status = 'CLOSED'
        """
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return 0.0

    wins = []
    losses = []

    for (pnl,) in rows:
        if pnl is None:
            continue
        if pnl > 0:
            wins.append(pnl)
        elif pnl < 0:
            losses.append(abs(pnl))

    if not wins and not losses:
        return 0.0

    if not wins:
        return 0.0

    if not losses and wins:
        return float("inf")

    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    payoff_ratio = avg_win / avg_loss

    return round(payoff_ratio, 4)


def get_average_win_loss():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl FROM positions
        WHERE status = 'CLOSED'
        """
    )

    rows = cursor.fetchall()
    conn.close()

    wins = []
    losses = []

    for (pnl,) in rows:
        if pnl is None:
            continue
        if pnl > 0:
            wins.append(pnl)
        elif pnl < 0:
            losses.append(pnl)

    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0

    return {
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "win_count": len(wins),
        "loss_count": len(losses),
    }


def get_gross_profit_loss():
    """
    Returns gross profit, gross loss (absolute), and net pnl
    using CLOSED trades only.
    """

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT pnl FROM positions
        WHERE status = 'CLOSED'
          AND pnl IS NOT NULL
    """)

    rows = cursor.fetchall()
    conn.close()

    gross_profit = 0.0
    gross_loss = 0.0

    for (pnl,) in rows:
        if pnl > 0:
            gross_profit += pnl
        elif pnl < 0:
            gross_loss += abs(pnl)

    net_pnl = gross_profit - gross_loss

    return {
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "net_pnl": round(net_pnl, 4),
    }


def get_average_trade_pnl() -> dict:
    """
    Average PnL per CLOSED trade (mean).
    CLOSED trades only, SQLite-only.
    Returns dict:
    - avg_trade_pnl (float, rounded to 4 decimals)
    - trade_count (int)
    Rules:
    - Use positions where status='CLOSED'
    - Ignore pnl is NULL
    - Include pnl == 0 (it should count as a trade)
    - If no CLOSED trades -> avg_trade_pnl=0.0, trade_count=0
    """

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*), AVG(pnl)
        FROM positions
        WHERE status = 'CLOSED'
          AND pnl IS NOT NULL
        """
    )

    row = cursor.fetchone()
    conn.close()

    trade_count = int(row[0]) if row and row[0] is not None else 0
    avg_trade_pnl = float(row[1]) if row and row[1] is not None else 0.0

    return {
        "avg_trade_pnl": round(avg_trade_pnl, 4),
        "trade_count": trade_count,
    }


def get_best_worst_trade():
    """
    Returns:
    {
    "best_trade_pnl": float,
    "worst_trade_pnl": float,
    "trade_count": int
    }
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl
        FROM positions
        WHERE status = 'CLOSED'
        AND pnl IS NOT NULL
    """
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {
            "best_trade_pnl": 0.0,
            "worst_trade_pnl": 0.0,
            "trade_count": 0,
        }

    pnls = [row[0] for row in rows]

    return {
        "best_trade_pnl": round(max(pnls), 4),
        "worst_trade_pnl": round(min(pnls), 4),
        "trade_count": len(pnls),
    }


def get_median_trade_pnl():
    """
    Returns median PnL of CLOSED trades.
    Includes pnl == 0.
    Ignores pnl IS NULL.
    No trades -> 0.0
    """
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl
        FROM positions
        WHERE status = 'CLOSED'
        AND pnl IS NOT NULL
        ORDER BY pnl ASC
    """
    )

    rows = cursor.fetchall()
    conn.close()

    pnls = [row[0] for row in rows]

    if not pnls:
        return {
            "median_trade_pnl": 0.0,
            "trade_count": 0
        }

    n = len(pnls)
    mid = n // 2

    if n % 2 == 1:
        median = pnls[mid]
    else:
        median = (pnls[mid - 1] + pnls[mid]) / 2

    return {
        "median_trade_pnl": round(float(median), 4),
        "trade_count": n
    }


def get_trade_pnl_std_dev():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl
        FROM positions
        WHERE status = 'CLOSED'
        AND pnl IS NOT NULL
        """
    )

    rows = cursor.fetchall()
    conn.close()

    pnls = [row[0] for row in rows]
    trade_count = len(pnls)

    if trade_count == 0:
        return {
            "std_dev": 0.0,
            "trade_count": 0,
        }

    if trade_count == 1:
        return {
            "std_dev": 0.0,
            "trade_count": 1,
        }

    mean = sum(pnls) / trade_count
    variance = sum((pnl - mean) ** 2 for pnl in pnls) / trade_count
    std_dev = variance ** 0.5

    return {
        "std_dev": round(float(std_dev), 4),
        "trade_count": trade_count,
    }


def get_trade_pnl_variance():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl
        FROM positions
        WHERE status = 'CLOSED'
        AND pnl IS NOT NULL
        """
    )

    rows = cursor.fetchall()
    conn.close()

    pnls = [row[0] for row in rows]
    trade_count = len(pnls)

    if trade_count == 0:
        return {
            "variance": 0.0,
            "trade_count": 0,
        }

    if trade_count == 1:
        return {
            "variance": 0.0,
            "trade_count": 1,
        }

    mean = sum(pnls) / trade_count
    variance = sum((pnl - mean) ** 2 for pnl in pnls) / trade_count

    return {
        "variance": round(float(variance), 4),
        "trade_count": trade_count,
    }


def get_trade_pnl_coefficient_of_variation():
    """
    Returns population coefficient of variation (std_dev / |mean|)
    for CLOSED trades.

    Returns dict:
    {
        "coefficient_of_variation": float,
        "trade_count": int
    }
    """
    import math

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl
        FROM positions
        WHERE status = 'CLOSED'
        AND pnl IS NOT NULL
        """
    )

    rows = cursor.fetchall()
    conn.close()

    pnls = [row[0] for row in rows]

    trade_count = len(pnls)

    if trade_count == 0:
        return {
            "coefficient_of_variation": 0.0,
            "trade_count": 0
        }

    mean = sum(pnls) / trade_count

    if mean == 0:
        return {
            "coefficient_of_variation": 0.0,
            "trade_count": trade_count
        }

    variance = sum((p - mean) ** 2 for p in pnls) / trade_count
    std_dev = math.sqrt(variance)

    cv = std_dev / abs(mean)

    return {
        "coefficient_of_variation": round(cv, 4),
        "trade_count": trade_count
    }


def get_downside_deviation():
    """
    Calculates population downside deviation (loss-only standard deviation)
    of CLOSED trade pnl values.
    """
    import math

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl
        FROM positions
        WHERE status = 'CLOSED'
        AND pnl IS NOT NULL
        AND pnl < 0
        """
    )

    rows = cursor.fetchall()
    conn.close()

    losses = [row[0] for row in rows]

    loss_count = len(losses)

    if loss_count <= 1:
        return {
            "downside_deviation": 0.0,
            "loss_count": loss_count
        }

    mean_loss = sum(losses) / loss_count
    variance = sum((p - mean_loss) ** 2 for p in losses) / loss_count
    downside_deviation = math.sqrt(variance)

    return {
        "downside_deviation": round(downside_deviation, 4),
        "loss_count": loss_count
    }


def get_upside_deviation():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT pnl
        FROM positions
        WHERE status = 'CLOSED'
        AND pnl IS NOT NULL
        AND pnl > 0
        """
    )

    rows = cursor.fetchall()
    conn.close()

    wins = [row[0] for row in rows]
    win_count = len(wins)

    if win_count <= 1:
        return {
            "upside_deviation": 0.0,
            "win_count": win_count
        }

    import math

    mean = sum(wins) / win_count
    variance = sum((p - mean) ** 2 for p in wins) / win_count
    upside_deviation = math.sqrt(variance)

    return {
        "upside_deviation": round(upside_deviation, 4),
        "win_count": win_count
    }


def get_closed_trades_for_export() -> list[dict[str, Any]]:
    """
    Returns CLOSED trades as export-ready rows for analysis.
    Read-only helper. SQLite only.
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                id,
                token_address,
                symbol,
                entry_price,
                exit_price,
                amount,
                usd_size,
                pnl,
                status,
                created_at,
                exit_time
            FROM positions
            WHERE status = 'CLOSED'
            ORDER BY id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
