import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def _safe_timestamp(timestamp_utc: str | None = None) -> str:
    if timestamp_utc is None:
        timestamp_utc = datetime.now(timezone.utc).isoformat()
    return str(timestamp_utc).replace(":", "-").replace(".", "-").replace("+", "_plus_")


def build_service_rollup_export_json_path(output_dir: str, prefix: str = "prelive_service_rollup", timestamp_utc: str | None = None) -> str:
    return str(Path(output_dir) / f"{prefix}_{_safe_timestamp(timestamp_utc)}.json")


def build_service_rollup_export_csv_path(output_dir: str, prefix: str = "prelive_service_rollup", timestamp_utc: str | None = None) -> str:
    return str(Path(output_dir) / f"{prefix}_{_safe_timestamp(timestamp_utc)}.csv")


def save_service_rollup_json(rollup_payload: dict, output_path: str) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rollup_payload, sort_keys=True), encoding="utf-8")
    return str(path)


def save_service_rollup_csv(rollup_payload: dict, output_path: str) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flat = {
        "loop_name": rollup_payload.get("loop_name"),
        "iteration": rollup_payload.get("iteration"),
        "candidate_preset_name": rollup_payload.get("candidate_preset_name"),
    }
    rollup = rollup_payload.get("rollup") or {}
    for key, value in rollup.items():
        flat[key] = value

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(flat.keys()))
        writer.writeheader()
        writer.writerow(flat)
    return str(path)
