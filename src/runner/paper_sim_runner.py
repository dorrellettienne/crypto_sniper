import argparse
import csv
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, r"C:\Users\Main_User\Desktop\crypto_sniper")

from src.execution.persistence import init_db, get_open_positions
from src.execution.paper_engine import simulate_buy
from src.execution.paper_engine import simulate_stop_loss
from src.execution.paper_engine import simulate_sell
from src.execution.paper_engine import simulate_time_exit
from src.execution.paper_engine import simulate_check_stop_trigger
from src.execution.persistence import get_today_trade_summary


def format_simulation_summary(result: dict) -> dict:
    """
    Returns a normalized, JSON-serializable summary dict for a simulation result.
    Read-only transform only. No DB access. No file writes.
    """
    source_summary = result.get("summary") or {}
    normalized_summary = {
        "total_trades": int(source_summary.get("total_trades", 0) or 0),
        "total_pnl": float(source_summary.get("total_pnl", 0.0) or 0.0),
        "wins": int(source_summary.get("wins", 0) or 0),
        "losses": int(source_summary.get("losses", 0) or 0),
        "win_rate": float(source_summary.get("win_rate", 0.0) or 0.0),
    }

    return {
        "steps": result.get("steps"),
        "seed": result.get("seed"),
        "actions_taken": result.get("actions_taken"),
        "generated_at_utc": result.get("generated_at_utc"),
        "summary": normalized_summary,
    }


def format_simulation_summary_json(result: dict) -> str:
    """
    Returns a deterministic JSON string for a simulation result summary.
    Read-only transform only. No DB access. No file writes.
    """
    normalized = format_simulation_summary(result)
    return json.dumps(normalized, sort_keys=True)


def format_simulation_summary_csv_row(result: dict) -> dict:
    """
    Returns a flat CSV-row dict for a simulation result summary.
    Read-only transform only. No DB access. No file writes.
    """
    normalized = format_simulation_summary(result)
    summary = normalized["summary"]
    return {
        "steps": normalized["steps"],
        "seed": normalized["seed"],
        "actions_taken": normalized["actions_taken"],
        "generated_at_utc": normalized["generated_at_utc"],
        "total_trades": summary["total_trades"],
        "total_pnl": summary["total_pnl"],
        "wins": summary["wins"],
        "losses": summary["losses"],
        "win_rate": summary["win_rate"],
    }


def build_simulation_summary_export_path(
    output_dir: str,
    prefix: str = "paper_sim_summary",
    timestamp_utc: str | None = None,
) -> str:
    """
    Builds a deterministic JSON export path for a simulation summary.
    String/path helper only. No file writes.
    """
    if timestamp_utc is None:
        timestamp_utc = datetime.now(timezone.utc).isoformat()

    safe_timestamp = (
        str(timestamp_utc)
        .replace(":", "-")
        .replace(".", "-")
        .replace("+", "_plus_")
    )
    filename = f"{prefix}_{safe_timestamp}.json"
    return str(Path(output_dir) / filename)


def build_simulation_summary_export_csv_path(
    output_dir: str,
    prefix: str = "paper_sim_summary",
    timestamp_utc: str | None = None,
) -> str:
    """
    Builds a deterministic CSV export path for a simulation summary row.
    String/path helper only. No file writes.
    """
    if timestamp_utc is None:
        timestamp_utc = datetime.now(timezone.utc).isoformat()

    safe_timestamp = (
        str(timestamp_utc)
        .replace(":", "-")
        .replace(".", "-")
        .replace("+", "_plus_")
    )
    filename = f"{prefix}_{safe_timestamp}.csv"
    return str(Path(output_dir) / filename)


def save_simulation_summary_json(result: dict, output_path: str) -> str:
    """
    Saves the normalized simulation summary JSON string to disk.
    Returns the written path.
    """
    json_str = format_simulation_summary_json(result)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_str, encoding="utf-8")
    return str(path)


def save_simulation_summary_csv(result: dict, output_path: str) -> str:
    """
    Saves a single-row CSV summary export.
    Returns the written path.
    """
    row = format_simulation_summary_csv_row(result)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(row.keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)
    return str(path)


def run_simulation(steps: int, seed: int, usd_size: float = 50.0):
    if steps < 0:
        raise ValueError("steps must be >= 0")
    if usd_size <= 0:
        raise ValueError("usd_size must be > 0")

    random.seed(seed)

    init_db()

    actions_taken = 0

    for _ in range(steps):
        r = random.random()

        if r < 0.2:
            position_id = simulate_buy(
                token_address="SIM_TOKEN",
                symbol="SIM",
                entry_price=0.01,
                usd_size=usd_size
            )
            if position_id is not None:
                actions_taken += 1
        elif r < 0.4:
            open_positions = get_open_positions()
            if open_positions:
                position_id = open_positions[0]["id"]
                pnl = simulate_stop_loss(position_id, 0.1)
                if pnl is not None:
                    actions_taken += 1
        elif r < 0.6:
            open_positions = get_open_positions()
            if open_positions:
                position_id = open_positions[0]["id"]
                pnl = simulate_sell(position_id, 0.02)
                if pnl is not None:
                    actions_taken += 1
        elif r < 0.8:
            open_positions = get_open_positions()
            if open_positions:
                position_id = open_positions[0]["id"]
                pnl = simulate_check_stop_trigger(position_id, 0.009)
                if pnl is not None:
                    actions_taken += 1
        else:
            open_positions = get_open_positions()
            if open_positions:
                position_id = open_positions[0]["id"]
                pnl = simulate_time_exit(position_id, 0.01)
                if pnl is not None:
                    actions_taken += 1

    summary = get_today_trade_summary()

    return {
        "steps": steps,
        "seed": seed,
        "actions_taken": actions_taken,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--usd-size", type=float, default=50.0)
    parser.add_argument("--export-json-path", type=str, default=None)
    parser.add_argument("--export-json-dir", type=str, default=None)
    parser.add_argument("--export-csv-path", type=str, default=None)
    parser.add_argument("--export-csv-dir", type=str, default=None)

    args = parser.parse_args()

    result = run_simulation(args.steps, args.seed, usd_size=args.usd_size)
    export_path = None
    export_csv_path = None
    if args.export_json_path:
        export_path = save_simulation_summary_json(result, args.export_json_path)
    elif args.export_json_dir:
        generated_path = build_simulation_summary_export_path(args.export_json_dir)
        export_path = save_simulation_summary_json(result, generated_path)
    if args.export_csv_path:
        export_csv_path = save_simulation_summary_csv(result, args.export_csv_path)
    elif args.export_csv_dir:
        generated_csv_path = build_simulation_summary_export_csv_path(args.export_csv_dir)
        export_csv_path = save_simulation_summary_csv(result, generated_csv_path)

    print("=== PAPER SIM RUN COMPLETE ===")
    print(f"Steps: {result['steps']}")
    print(f"Seed: {result['seed']}")
    print(f"USD Size: {args.usd_size}")
    print(f"Actions Taken: {result['actions_taken']}")
    print("Daily Summary:", format_simulation_summary(result)["summary"])
    if export_path:
        print(f"Exported JSON: {export_path}")
    if export_csv_path:
        print(f"Exported CSV: {export_csv_path}")
