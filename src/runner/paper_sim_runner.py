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
from src.execution.persistence import get_today_trade_summary, get_closed_trades_for_export
from src.live.cost_model import estimate_net_pnl_from_gross
from src.live.path_security import ensure_dir_within_base, ensure_path_within_base


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
    source_cost = result.get("cost_estimate") or {}
    if source_cost:
        normalized_summary["estimated_total_cost_usd"] = float(source_cost.get("estimated_total_cost_usd", 0.0) or 0.0)
        normalized_summary["estimated_net_pnl"] = float(source_cost.get("estimated_net_pnl", normalized_summary["total_pnl"]) or 0.0)

    normalized = {
        "steps": result.get("steps"),
        "seed": result.get("seed"),
        "actions_taken": result.get("actions_taken"),
        "generated_at_utc": result.get("generated_at_utc"),
        "summary": normalized_summary,
    }
    if source_cost:
        normalized["cost_model"] = {
            "fee_bps_per_leg": float(source_cost.get("fee_bps_per_leg", 0.0) or 0.0),
            "slippage_bps_per_leg": float(source_cost.get("slippage_bps_per_leg", 0.0) or 0.0),
            "network_fee_usd_per_leg": float(source_cost.get("network_fee_usd_per_leg", 0.0) or 0.0),
        }
    return normalized


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
    row = {
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
    if "estimated_total_cost_usd" in summary:
        row["estimated_total_cost_usd"] = float(summary["estimated_total_cost_usd"])
        row["estimated_net_pnl"] = float(summary.get("estimated_net_pnl", summary["total_pnl"]))
    return row


def add_cost_estimate_to_result(
    result: dict,
    entry_notional_usd: float,
    fee_bps_per_leg: float = 0.0,
    slippage_bps_per_leg: float = 0.0,
    network_fee_usd_per_leg: float = 0.0,
) -> dict:
    """
    Returns a copy of a simulation result enriched with estimated cost/net PnL fields.
    Read-only transform only.
    """
    enriched = dict(result)
    gross_pnl = float((result.get("summary") or {}).get("total_pnl", 0.0) or 0.0)
    # Approximate exit notional as entry notional plus gross pnl, clamped at zero.
    exit_notional_usd = max(0.0, float(entry_notional_usd) + gross_pnl)
    estimated = estimate_net_pnl_from_gross(
        gross_pnl=gross_pnl,
        entry_notional_usd=float(entry_notional_usd),
        exit_notional_usd=exit_notional_usd,
        fee_bps_per_leg=float(fee_bps_per_leg),
        slippage_bps_per_leg=float(slippage_bps_per_leg),
        network_fee_usd_per_leg=float(network_fee_usd_per_leg),
    )
    enriched["cost_estimate"] = {
        "fee_bps_per_leg": float(fee_bps_per_leg),
        "slippage_bps_per_leg": float(slippage_bps_per_leg),
        "network_fee_usd_per_leg": float(network_fee_usd_per_leg),
        "estimated_total_cost_usd": float(estimated["estimated_total_cost_usd"]),
        "estimated_net_pnl": float(estimated["estimated_net_pnl"]),
    }
    return enriched


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


def build_closed_trades_export_csv_path(
    output_dir: str,
    prefix: str = "paper_sim_closed_trades",
    timestamp_utc: str | None = None,
) -> str:
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


def save_closed_trades_csv(output_path: str) -> str:
    """
    Exports all CLOSED trades from SQLite to CSV for detailed analysis.
    Read-only DB query + file write helper.
    """
    rows = get_closed_trades_for_export()
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id",
        "token_address",
        "symbol",
        "entry_price",
        "exit_price",
        "amount",
        "usd_size",
        "pnl",
        "status",
        "created_at",
        "exit_time",
    ]

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})

    return str(path)


def _validate_branch_probabilities(
    p_buy: float,
    p_stop_loss: float,
    p_sell: float,
    p_stop_check: float,
    p_time_exit: float,
) -> None:
    probs = [p_buy, p_stop_loss, p_sell, p_stop_check, p_time_exit]
    if any(p < 0 for p in probs):
        raise ValueError("branch probabilities must be >= 0")

    total = sum(probs)
    if abs(total - 1.0) > 1e-9:
        raise ValueError("branch probabilities must sum to 1.0")


def run_simulation(
    steps: int,
    seed: int,
    usd_size: float = 50.0,
    stop_loss_percent: float = 0.1,
    sell_price: float = 0.02,
    p_buy: float = 0.2,
    p_stop_loss: float = 0.2,
    p_sell: float = 0.2,
    p_stop_check: float = 0.2,
    p_time_exit: float = 0.2,
):
    if steps < 0:
        raise ValueError("steps must be >= 0")
    if usd_size <= 0:
        raise ValueError("usd_size must be > 0")
    if stop_loss_percent <= 0:
        raise ValueError("stop_loss_percent must be > 0")
    if sell_price <= 0:
        raise ValueError("sell_price must be > 0")
    _validate_branch_probabilities(
        p_buy,
        p_stop_loss,
        p_sell,
        p_stop_check,
        p_time_exit,
    )

    random.seed(seed)

    init_db()

    actions_taken = 0
    b1 = p_buy
    b2 = b1 + p_stop_loss
    b3 = b2 + p_sell
    b4 = b3 + p_stop_check

    for _ in range(steps):
        r = random.random()

        if r < b1:
            position_id = simulate_buy(
                token_address="SIM_TOKEN",
                symbol="SIM",
                entry_price=0.01,
                usd_size=usd_size
            )
            if position_id is not None:
                actions_taken += 1
        elif r < b2:
            open_positions = get_open_positions()
            if open_positions:
                position_id = open_positions[0]["id"]
                pnl = simulate_stop_loss(position_id, stop_loss_percent)
                if pnl is not None:
                    actions_taken += 1
        elif r < b3:
            open_positions = get_open_positions()
            if open_positions:
                position_id = open_positions[0]["id"]
                pnl = simulate_sell(position_id, sell_price)
                if pnl is not None:
                    actions_taken += 1
        elif r < b4:
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
    parser.add_argument("--stop-loss-percent", type=float, default=0.1)
    parser.add_argument("--sell-price", type=float, default=0.02)
    parser.add_argument("--p-buy", type=float, default=0.2)
    parser.add_argument("--p-stop-loss", type=float, default=0.2)
    parser.add_argument("--p-sell", type=float, default=0.2)
    parser.add_argument("--p-stop-check", type=float, default=0.2)
    parser.add_argument("--p-time-exit", type=float, default=0.2)
    parser.add_argument("--export-json-path", type=str, default=None)
    parser.add_argument("--export-json-dir", type=str, default=None)
    parser.add_argument("--export-csv-path", type=str, default=None)
    parser.add_argument("--export-csv-dir", type=str, default=None)
    parser.add_argument("--export-trades-csv-path", type=str, default=None)
    parser.add_argument("--export-trades-csv-dir", type=str, default=None)
    parser.add_argument("--allow-unsafe-paths", action="store_true")
    parser.add_argument("--estimate-fee-bps", type=float, default=0.0)
    parser.add_argument("--estimate-slippage-bps", type=float, default=0.0)
    parser.add_argument("--estimate-network-fee-usd", type=float, default=0.0)

    args = parser.parse_args()

    if not args.allow_unsafe_paths:
        for file_path in [args.export_json_path, args.export_csv_path, args.export_trades_csv_path]:
            if file_path:
                ensure_path_within_base(file_path)
        for dir_path in [args.export_json_dir, args.export_csv_dir, args.export_trades_csv_dir]:
            if dir_path:
                ensure_dir_within_base(dir_path)

    result = run_simulation(
        args.steps,
        args.seed,
        usd_size=args.usd_size,
        stop_loss_percent=args.stop_loss_percent,
        sell_price=args.sell_price,
        p_buy=args.p_buy,
        p_stop_loss=args.p_stop_loss,
        p_sell=args.p_sell,
        p_stop_check=args.p_stop_check,
        p_time_exit=args.p_time_exit,
    )
    if args.estimate_fee_bps or args.estimate_slippage_bps or args.estimate_network_fee_usd:
        result = add_cost_estimate_to_result(
            result,
            entry_notional_usd=args.usd_size,
            fee_bps_per_leg=args.estimate_fee_bps,
            slippage_bps_per_leg=args.estimate_slippage_bps,
            network_fee_usd_per_leg=args.estimate_network_fee_usd,
        )
    export_path = None
    export_csv_path = None
    export_trades_csv_path = None
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
    if args.export_trades_csv_path:
        export_trades_csv_path = save_closed_trades_csv(args.export_trades_csv_path)
    elif args.export_trades_csv_dir:
        generated_trades_csv_path = build_closed_trades_export_csv_path(args.export_trades_csv_dir)
        export_trades_csv_path = save_closed_trades_csv(generated_trades_csv_path)

    print("=== PAPER SIM RUN COMPLETE ===")
    print(f"Steps: {result['steps']}")
    print(f"Seed: {result['seed']}")
    print(f"USD Size: {args.usd_size}")
    print(f"Stop Loss Percent: {args.stop_loss_percent}")
    print(f"Sell Price: {args.sell_price}")
    print(
        "Branch Probs:",
        {
            "buy": args.p_buy,
            "stop_loss": args.p_stop_loss,
            "sell": args.p_sell,
            "stop_check": args.p_stop_check,
            "time_exit": args.p_time_exit,
        },
    )
    print(f"Actions Taken: {result['actions_taken']}")
    print("Daily Summary:", format_simulation_summary(result)["summary"])
    if result.get("cost_estimate"):
        print("Estimated Cost Model:", result["cost_estimate"])
    if export_path:
        print(f"Exported JSON: {export_path}")
    if export_csv_path:
        print(f"Exported CSV: {export_csv_path}")
    if export_trades_csv_path:
        print(f"Exported Closed Trades CSV: {export_trades_csv_path}")
