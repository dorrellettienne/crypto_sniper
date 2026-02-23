import argparse
import json
from pathlib import Path

from src.live.audit_logger import append_audit_event, build_audit_log_path
from src.live.config_validation import validate_candidate_preset_config
from src.runner.paper_sim_runner import (
    build_closed_trades_export_csv_path,
    build_simulation_summary_export_csv_path,
    build_simulation_summary_export_path,
    run_simulation,
    save_closed_trades_csv,
    save_simulation_summary_csv,
    save_simulation_summary_json,
)


DEFAULT_CANDIDATE_PRESETS_PATH = "config/sniping_presets_candidates_v1.json"
DEFAULT_CANDIDATE_PRESET_NAME = "candidate_final_v1_tp_higher_034"


def load_candidate_presets(path: str = DEFAULT_CANDIDATE_PRESETS_PATH) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    presets = payload.get("presets") if isinstance(payload, dict) else payload
    if not isinstance(presets, list) or not presets:
        raise ValueError("candidate presets file must contain a non-empty presets list")
    return [dict(preset) for preset in presets]


def get_candidate_preset(
    preset_name: str | None = None,
    presets_path: str = DEFAULT_CANDIDATE_PRESETS_PATH,
) -> dict:
    presets = load_candidate_presets(presets_path)
    if preset_name is None:
        preset_name = DEFAULT_CANDIDATE_PRESET_NAME

    for preset in presets:
        if str(preset.get("name")) == preset_name:
            return preset
    raise ValueError(f"preset not found: {preset_name}")


def run_candidate_preset(
    preset_name: str | None = None,
    presets_path: str = DEFAULT_CANDIDATE_PRESETS_PATH,
    steps: int = 200,
    seed: int = 1,
    export_json_dir: str | None = None,
    export_csv_dir: str | None = None,
    export_trades_csv_dir: str | None = None,
    audit_log_dir: str | None = "data/exports",
) -> dict:
    preset = get_candidate_preset(preset_name=preset_name, presets_path=presets_path)
    preset = validate_candidate_preset_config(preset)
    audit_log_path = None
    if audit_log_dir:
        audit_log_path = build_audit_log_path(audit_log_dir, prefix=f"{preset['name']}_audit")
        append_audit_event(
            audit_log_path,
            "run_started",
            {
                "preset_name": preset["name"],
                "steps": steps,
                "seed": seed,
                "presets_path": presets_path,
            },
        )

    try:
        result = run_simulation(
            steps=steps,
            seed=seed,
            usd_size=float(preset["usd_size"]),
            stop_loss_percent=float(preset["stop_loss_percent"]),
            sell_price=float(preset["sell_price"]),
            p_buy=float(preset["p_buy"]),
            p_stop_loss=float(preset["p_stop_loss"]),
            p_sell=float(preset["p_sell"]),
            p_stop_check=float(preset["p_stop_check"]),
            p_time_exit=float(preset["p_time_exit"]),
        )
    except Exception as exc:
        if audit_log_path:
            append_audit_event(
                audit_log_path,
                "run_failed",
                {"preset_name": preset["name"], "error": str(exc)},
            )
        raise

    written_json = None
    written_csv = None
    written_trades_csv = None

    if export_json_dir:
        json_path = build_simulation_summary_export_path(export_json_dir, prefix=f"{preset['name']}_summary")
        written_json = save_simulation_summary_json(result, json_path)
        if audit_log_path:
            append_audit_event(audit_log_path, "export_written", {"type": "summary_json", "path": written_json})
    if export_csv_dir:
        csv_path = build_simulation_summary_export_csv_path(export_csv_dir, prefix=f"{preset['name']}_summary")
        written_csv = save_simulation_summary_csv(result, csv_path)
        if audit_log_path:
            append_audit_event(audit_log_path, "export_written", {"type": "summary_csv", "path": written_csv})
    if export_trades_csv_dir:
        trades_path = build_closed_trades_export_csv_path(export_trades_csv_dir, prefix=f"{preset['name']}_closed_trades")
        written_trades_csv = save_closed_trades_csv(trades_path)
        if audit_log_path:
            append_audit_event(audit_log_path, "export_written", {"type": "closed_trades_csv", "path": written_trades_csv})

    if audit_log_path:
        append_audit_event(
            audit_log_path,
            "run_completed",
            {
                "preset_name": preset["name"],
                "steps": result.get("steps"),
                "seed": result.get("seed"),
                "actions_taken": result.get("actions_taken"),
                "summary": result.get("summary"),
            },
        )

    return {
        "preset": preset,
        "result": result,
        "export_json_path": written_json,
        "export_csv_path": written_csv,
        "export_trades_csv_path": written_trades_csv,
        "audit_log_path": audit_log_path,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset-name", type=str, default=DEFAULT_CANDIDATE_PRESET_NAME)
    parser.add_argument("--presets-json-path", type=str, default=DEFAULT_CANDIDATE_PRESETS_PATH)
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--export-json-dir", type=str, default="data/exports")
    parser.add_argument("--export-csv-dir", type=str, default="data/exports")
    parser.add_argument("--export-trades-csv-dir", type=str, default="data/exports")
    parser.add_argument("--audit-log-dir", type=str, default="data/exports")
    args = parser.parse_args()

    output = run_candidate_preset(
        preset_name=args.preset_name,
        presets_path=args.presets_json_path,
        steps=args.steps,
        seed=args.seed,
        export_json_dir=args.export_json_dir,
        export_csv_dir=args.export_csv_dir,
        export_trades_csv_dir=args.export_trades_csv_dir,
        audit_log_dir=args.audit_log_dir,
    )

    print("=== PAPER CANDIDATE RUN COMPLETE ===")
    print(f"Preset: {output['preset']['name']}")
    print(f"Steps: {output['result']['steps']}")
    print(f"Seed: {output['result']['seed']}")
    print(f"Actions Taken: {output['result']['actions_taken']}")
    print(f"Summary: {output['result']['summary']}")
    if output["export_json_path"]:
        print(f"Exported JSON: {output['export_json_path']}")
    if output["export_csv_path"]:
        print(f"Exported CSV: {output['export_csv_path']}")
    if output["export_trades_csv_path"]:
        print(f"Exported Closed Trades CSV: {output['export_trades_csv_path']}")
    if output["audit_log_path"]:
        print(f"Audit Log: {output['audit_log_path']}")
