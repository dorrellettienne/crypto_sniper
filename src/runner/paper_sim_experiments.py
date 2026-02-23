import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.execution.persistence import DB_PATH
from src.runner.paper_sim_runner import format_simulation_summary, run_simulation


def _reset_positions_for_experiment_run() -> None:
    """
    Clears positions so each seed run starts from the same paper-mode baseline.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def run_seed_sweep(
    steps: int,
    seeds: list[int],
    usd_size: float = 50.0,
    stop_loss_percent: float = 0.1,
    sell_price: float = 0.02,
    p_buy: float = 0.2,
    p_stop_loss: float = 0.2,
    p_sell: float = 0.2,
    p_stop_check: float = 0.2,
    p_time_exit: float = 0.2,
) -> list[dict]:
    """
    Runs deterministic paper simulations across multiple seeds.
    Returns normalized summaries for comparison.
    """
    results = []
    for seed in seeds:
        _reset_positions_for_experiment_run()
        sim_result = run_simulation(
            steps=steps,
            seed=seed,
            usd_size=usd_size,
            stop_loss_percent=stop_loss_percent,
            sell_price=sell_price,
            p_buy=p_buy,
            p_stop_loss=p_stop_loss,
            p_sell=p_sell,
            p_stop_check=p_stop_check,
            p_time_exit=p_time_exit,
        )
        normalized = format_simulation_summary(sim_result)
        normalized["seed"] = seed
        results.append(normalized)
    return results


def summarize_seed_sweep(runs: list[dict]) -> dict:
    """
    Builds a compact aggregate summary from normalized seed-sweep runs.
    """
    if not runs:
        return {
            "run_count": 0,
            "avg_total_pnl": 0.0,
            "avg_total_trades": 0.0,
            "avg_win_rate": 0.0,
            "best_total_pnl": 0.0,
            "worst_total_pnl": 0.0,
        }

    pnls = [float(run["summary"]["total_pnl"]) for run in runs]
    trades = [float(run["summary"]["total_trades"]) for run in runs]
    win_rates = [float(run["summary"]["win_rate"]) for run in runs]

    return {
        "run_count": len(runs),
        "avg_total_pnl": round(sum(pnls) / len(pnls), 4),
        "avg_total_trades": round(sum(trades) / len(trades), 4),
        "avg_win_rate": round(sum(win_rates) / len(win_rates), 4),
        "best_total_pnl": round(max(pnls), 4),
        "worst_total_pnl": round(min(pnls), 4),
    }


def run_preset_seed_sweep_batch(
    steps: int,
    seeds: list[int],
    presets: list[dict],
) -> list[dict]:
    """
    Runs multiple rule presets across a shared seed list.
    Returns one aggregate row per preset for easy comparison/export.
    """
    batch_rows = []
    for preset in presets:
        preset_name = str(preset.get("name", "unnamed"))
        sweep_runs = run_seed_sweep(
            steps=steps,
            seeds=seeds,
            usd_size=float(preset.get("usd_size", 50.0)),
            stop_loss_percent=float(preset.get("stop_loss_percent", 0.1)),
            sell_price=float(preset.get("sell_price", 0.02)),
            p_buy=float(preset.get("p_buy", 0.2)),
            p_stop_loss=float(preset.get("p_stop_loss", 0.2)),
            p_sell=float(preset.get("p_sell", 0.2)),
            p_stop_check=float(preset.get("p_stop_check", 0.2)),
            p_time_exit=float(preset.get("p_time_exit", 0.2)),
        )
        aggregate = summarize_seed_sweep(sweep_runs)
        aggregate_row = {
            "preset_name": preset_name,
            "steps": int(steps),
            "seed_count": len(seeds),
            "usd_size": float(preset.get("usd_size", 50.0)),
            "stop_loss_percent": float(preset.get("stop_loss_percent", 0.1)),
            "sell_price": float(preset.get("sell_price", 0.02)),
            "p_buy": float(preset.get("p_buy", 0.2)),
            "p_stop_loss": float(preset.get("p_stop_loss", 0.2)),
            "p_sell": float(preset.get("p_sell", 0.2)),
            "p_stop_check": float(preset.get("p_stop_check", 0.2)),
            "p_time_exit": float(preset.get("p_time_exit", 0.2)),
            **aggregate,
        }
        batch_rows.append(aggregate_row)
    return batch_rows


def rank_preset_batch_rows(rows: list[dict]) -> list[dict]:
    """
    Returns a new list of preset aggregate rows sorted for comparison and
    annotated with 1-based rank.
    Sorting priority:
    1) avg_total_pnl desc
    2) worst_total_pnl desc (less-bad downside ranks higher)
    3) avg_win_rate desc
    4) preset_name asc
    """
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            -float(row.get("avg_total_pnl", 0.0) or 0.0),
            -float(row.get("worst_total_pnl", 0.0) or 0.0),
            -float(row.get("avg_win_rate", 0.0) or 0.0),
            str(row.get("preset_name", "")),
        ),
    )

    ranked = []
    for idx, row in enumerate(sorted_rows, start=1):
        ranked_row = dict(row)
        ranked_row["rank"] = idx
        ranked.append(ranked_row)
    return ranked


def build_preset_batch_summary_csv_path(
    output_dir: str,
    prefix: str = "paper_sim_preset_batch_summary",
    timestamp_utc: str | None = None,
) -> str:
    """
    Builds a deterministic CSV export path for preset-batch aggregate rows.
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


def save_preset_batch_summary_csv(rows: list[dict], output_path: str) -> str:
    """
    Saves aggregate preset comparison rows to CSV.
    Returns the written path.
    """
    fieldnames = [
        "rank",
        "preset_name",
        "steps",
        "seed_count",
        "usd_size",
        "stop_loss_percent",
        "sell_price",
        "p_buy",
        "p_stop_loss",
        "p_sell",
        "p_stop_check",
        "p_time_exit",
        "run_count",
        "avg_total_pnl",
        "avg_total_trades",
        "avg_win_rate",
        "best_total_pnl",
        "worst_total_pnl",
    ]

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return str(path)
