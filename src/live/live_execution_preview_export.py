import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from src.live.audit_logger import append_audit_event, build_audit_log_path
from src.live.live_execution_adapter import LiveExecutionAdapter
from src.live.path_security import ensure_dir_within_base, ensure_path_within_base


def serialize_execution_result_preview(result) -> dict:
    """
    Convert an ExecutionResult into a JSON/audit-friendly preview payload.
    """
    metadata = dict(result.metadata or {})
    return {
        "action": result.action,
        "ok": bool(result.ok),
        "position_id": result.position_id,
        "pnl": result.pnl,
        "message": result.message,
        "metadata": metadata,
    }


def build_live_execution_preview_json_path(
    output_dir: str,
    prefix: str = "live_execution_preview",
    timestamp_utc: str | None = None,
) -> str:
    if timestamp_utc is None:
        timestamp_utc = datetime.now(timezone.utc).isoformat()
    safe_timestamp = str(timestamp_utc).replace(":", "-").replace(".", "-").replace("+", "_plus_")
    return str(Path(output_dir) / f"{prefix}_{safe_timestamp}.json")


def save_live_execution_preview_json(payload: dict, output_path: str) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return str(path)


def _emit_preview_events(audit_log_path: str, action: str, preview_payload: dict) -> None:
    append_audit_event(audit_log_path, "live_execution_preview", {"action": action, "preview": preview_payload})
    md = preview_payload.get("metadata") or {}
    if md.get("submit_preview") is not None:
        append_audit_event(
            audit_log_path,
            "live_submit_preview",
            {"action": action, "submit_preview": md.get("submit_preview"), "client_order_id": md.get("client_order_id")},
        )
    if md.get("confirmation_preview") is not None:
        append_audit_event(
            audit_log_path,
            "live_confirmation_preview",
            {"action": action, "confirmation_preview": md.get("confirmation_preview"), "client_order_id": md.get("client_order_id")},
        )


def run_live_execution_preview_export(
    output_json_path: str | None = None,
    output_json_dir: str | None = None,
    audit_log_dir: str = "data/exports",
) -> dict:
    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 10,
            "pilot_mode": True,
            "pilot_hard_max_order_usd_cap": 25,
            "audit_log_path": str(Path(audit_log_dir) / "pilot_audit.jsonl"),
            "candidate_preset_name": "candidate_final_v1_tp_higher_034",
            "live_submit_skeleton_enabled": True,
            "submit_skeleton_confirmation_outcomes": ["pending", "confirmed"],
        }
    )

    previews = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "live_execution_preview_skeleton",
        "buy": serialize_execution_result_preview(adapter.buy("TOKEN_A", "TKA", 0.01, 10)),
        "sell": serialize_execution_result_preview(adapter.sell(1, 0.02)),
        "stop_loss": serialize_execution_result_preview(adapter.stop_loss(1, 0.1)),
    }

    audit_log_path = build_audit_log_path(audit_log_dir, prefix="live_execution_preview_audit")
    append_audit_event(audit_log_path, "live_execution_preview_run_started", {"mode": previews["mode"]})
    _emit_preview_events(audit_log_path, "buy", previews["buy"])
    _emit_preview_events(audit_log_path, "sell", previews["sell"])
    _emit_preview_events(audit_log_path, "stop_loss", previews["stop_loss"])
    append_audit_event(audit_log_path, "live_execution_preview_run_completed", {"actions": ["buy", "sell", "stop_loss"]})

    written_json_path = None
    if output_json_path:
        written_json_path = save_live_execution_preview_json(previews, output_json_path)
    elif output_json_dir:
        generated_path = build_live_execution_preview_json_path(output_json_dir)
        written_json_path = save_live_execution_preview_json(previews, generated_path)

    return {
        "preview_json_path": written_json_path,
        "audit_log_path": audit_log_path,
        "previews": previews,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-json-path", type=str, default=None)
    parser.add_argument("--export-json-dir", type=str, default="data/exports")
    parser.add_argument("--audit-log-dir", type=str, default="data/exports")
    parser.add_argument("--allow-unsafe-paths", action="store_true")
    args = parser.parse_args()

    if not args.allow_unsafe_paths:
        if args.export_json_path:
            ensure_path_within_base(args.export_json_path)
        if args.export_json_dir:
            ensure_dir_within_base(args.export_json_dir)
        if args.audit_log_dir:
            ensure_dir_within_base(args.audit_log_dir)

    out = run_live_execution_preview_export(
        output_json_path=args.export_json_path,
        output_json_dir=None if args.export_json_path else args.export_json_dir,
        audit_log_dir=args.audit_log_dir,
    )
    print("=== LIVE EXECUTION PREVIEW EXPORT COMPLETE ===")
    print(f"Preview JSON: {out['preview_json_path']}")
    print(f"Audit Log: {out['audit_log_path']}")
