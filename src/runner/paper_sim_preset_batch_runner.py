import argparse
import json
from pathlib import Path

from src.runner.paper_sim_experiments import (
    build_preset_batch_summary_csv_path,
    rank_preset_batch_rows,
    run_preset_seed_sweep_batch,
    save_preset_batch_summary_csv,
)


DEFAULT_PRESETS = [
    {
        "name": "balanced",
        "usd_size": 50.0,
        "stop_loss_percent": 0.1,
        "sell_price": 0.02,
        "p_buy": 0.2,
        "p_stop_loss": 0.2,
        "p_sell": 0.2,
        "p_stop_check": 0.2,
        "p_time_exit": 0.2,
    },
    {
        "name": "target_heavy",
        "usd_size": 100.0,
        "stop_loss_percent": 0.12,
        "sell_price": 0.03,
        "p_buy": 0.3,
        "p_stop_loss": 0.15,
        "p_sell": 0.35,
        "p_stop_check": 0.1,
        "p_time_exit": 0.1,
    },
    {
        "name": "defensive",
        "usd_size": 75.0,
        "stop_loss_percent": 0.08,
        "sell_price": 0.02,
        "p_buy": 0.2,
        "p_stop_loss": 0.3,
        "p_sell": 0.2,
        "p_stop_check": 0.2,
        "p_time_exit": 0.1,
    },
]


def _parse_seeds(value: str) -> list[int]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        raise ValueError("at least one seed is required")
    return [int(part) for part in parts]


def load_presets_from_json(path: str) -> list[dict]:
    """
    Loads preset definitions from a JSON file.
    Accepts either:
    - a list of preset dicts
    - {"presets": [...]}
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    presets = payload.get("presets") if isinstance(payload, dict) else payload

    if not isinstance(presets, list) or not presets:
        raise ValueError("presets JSON must contain a non-empty list")

    normalized = []
    for idx, preset in enumerate(presets, start=1):
        if not isinstance(preset, dict):
            raise ValueError("each preset must be an object")
        row = dict(preset)
        if not row.get("name"):
            row["name"] = f"preset_{idx}"
        normalized.append(row)
    return normalized


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--seeds", type=str, default="1,2,3,4,5")
    parser.add_argument("--presets-json-path", type=str, default=None)
    parser.add_argument("--export-csv-path", type=str, default=None)
    parser.add_argument("--export-csv-dir", type=str, default=None)
    args = parser.parse_args()

    seeds = _parse_seeds(args.seeds)
    presets = load_presets_from_json(args.presets_json_path) if args.presets_json_path else DEFAULT_PRESETS
    rows = run_preset_seed_sweep_batch(steps=args.steps, seeds=seeds, presets=presets)
    ranked_rows = rank_preset_batch_rows(rows)

    export_path = None
    if args.export_csv_path:
        export_path = save_preset_batch_summary_csv(ranked_rows, args.export_csv_path)
    elif args.export_csv_dir:
        generated = build_preset_batch_summary_csv_path(args.export_csv_dir)
        export_path = save_preset_batch_summary_csv(ranked_rows, generated)

    print("=== PAPER SIM PRESET BATCH COMPLETE ===")
    print(f"Steps: {args.steps}")
    print(f"Seeds: {seeds}")
    print(f"Preset Count: {len(presets)}")
    print("Preset Aggregates:")
    for row in ranked_rows:
        print(
            {
                "rank": row["rank"],
                "preset_name": row["preset_name"],
                "avg_total_pnl": row["avg_total_pnl"],
                "avg_total_trades": row["avg_total_trades"],
                "avg_win_rate": row["avg_win_rate"],
                "best_total_pnl": row["best_total_pnl"],
                "worst_total_pnl": row["worst_total_pnl"],
            }
        )
    if export_path:
        print(f"Exported Preset Batch CSV: {export_path}")
