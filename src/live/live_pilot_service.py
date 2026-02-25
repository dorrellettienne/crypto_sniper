import argparse
import json
from pathlib import Path
from typing import Any

from src.live.audit_logger import append_audit_event, build_audit_log_path
from src.live.live_execution_adapter import LiveExecutionAdapter
from src.live.path_security import ensure_dir_within_base


def _rollup_update_from_dispatch(rollup: dict[str, Any], dispatch: dict[str, Any]) -> None:
    reason = str((dispatch or {}).get("reason") or "")
    if reason:
        by_reason = rollup.setdefault("submit_dispatch_by_reason", {})
        by_reason[reason] = int(by_reason.get(reason, 0)) + 1
    if (dispatch or {}).get("submitted_signature"):
        rollup["submitted_signatures"] = int(rollup.get("submitted_signatures", 0)) + 1
    if isinstance((dispatch or {}).get("pause_latch"), dict) and (dispatch["pause_latch"].get("latched") is True):
        rollup["pause_latch_events"] = int(rollup.get("pause_latch_events", 0)) + 1
    if isinstance((dispatch or {}).get("pause_reset"), dict) and (dispatch["pause_reset"].get("reset_applied") is True):
        rollup["pause_reset_events"] = int(rollup.get("pause_reset_events", 0)) + 1


def _audit_result_and_dispatch(audit_log_path: str, payload: dict[str, Any]) -> None:
    append_audit_event(audit_log_path, "live_pilot_execution_result", payload)
    md = payload.get("metadata") or {}
    if isinstance(md.get("manual_submit_gate"), dict):
        append_audit_event(audit_log_path, "live_manual_submit_gate", md["manual_submit_gate"])
    if isinstance(md.get("submit_dispatch"), dict):
        append_audit_event(audit_log_path, "live_submit_dispatch", md["submit_dispatch"])
        if isinstance(md["submit_dispatch"].get("pause_latch"), dict):
            append_audit_event(audit_log_path, "live_submit_pause_latch", md["submit_dispatch"]["pause_latch"])
        if isinstance(md["submit_dispatch"].get("pause_reset"), dict):
            append_audit_event(audit_log_path, "live_submit_pause_reset", md["submit_dispatch"]["pause_reset"])


def run_live_pilot_service_once(
    *,
    token_address: str,
    symbol: str,
    entry_price: float,
    usd_size: float,
    audit_log_dir: str = "data/exports",
    adapter_config: dict | None = None,
    rpc_client=None,
    dex_executor=None,
    rpc_transport=None,
    dex_quote_transport=None,
    dex_swap_transport=None,
) -> dict:
    cfg = dict(adapter_config or {})
    cfg.setdefault("live_enabled", True)
    cfg.setdefault("pilot_mode", True)
    cfg.setdefault("audit_log_path", str(Path(audit_log_dir) / "pilot_live_service_audit.jsonl"))
    adapter = LiveExecutionAdapter(
        cfg,
        rpc_client=rpc_client,
        dex_executor=dex_executor,
        rpc_transport=rpc_transport,
        dex_quote_transport=dex_quote_transport,
        dex_swap_transport=dex_swap_transport,
    )

    audit_log_path = build_audit_log_path(audit_log_dir, prefix="live_pilot_service")
    append_audit_event(audit_log_path, "live_pilot_service_started", {"mode": "one_shot"})

    result = adapter.buy(str(token_address), str(symbol), float(entry_price), float(usd_size))
    payload = {
        "ok": bool(result.ok),
        "action": result.action,
        "message": result.message,
        "metadata": dict(result.metadata or {}),
    }
    _audit_result_and_dispatch(audit_log_path, payload)
    md = payload["metadata"]

    rollup = {
        "runs": 1,
        "submit_dispatch_by_reason": {},
        "submitted_signatures": 0,
        "pause_latch_events": 0,
        "pause_reset_events": 0,
    }
    _rollup_update_from_dispatch(rollup, md.get("submit_dispatch") if isinstance(md.get("submit_dispatch"), dict) else {})
    append_audit_event(audit_log_path, "live_pilot_service_completed", {"rollup": rollup})
    return {"audit_log_path": audit_log_path, "result": payload, "rollup": rollup}


def run_live_pilot_service_loop(
    *,
    token_address: str,
    symbol: str,
    entry_price: float,
    usd_size: float,
    iterations: int,
    audit_log_dir: str = "data/exports",
    adapter_config: dict | None = None,
    rpc_client=None,
    dex_executor=None,
    rpc_transport=None,
    dex_quote_transport=None,
    dex_swap_transport=None,
) -> dict:
    iterations = int(iterations)
    if iterations <= 0:
        raise ValueError("iterations must be > 0")
    cfg = dict(adapter_config or {})
    cfg.setdefault("live_enabled", True)
    cfg.setdefault("pilot_mode", True)
    cfg.setdefault("audit_log_path", str(Path(audit_log_dir) / "pilot_live_service_audit.jsonl"))
    adapter = LiveExecutionAdapter(
        cfg,
        rpc_client=rpc_client,
        dex_executor=dex_executor,
        rpc_transport=rpc_transport,
        dex_quote_transport=dex_quote_transport,
        dex_swap_transport=dex_swap_transport,
    )

    audit_log_path = build_audit_log_path(audit_log_dir, prefix="live_pilot_service_loop")
    append_audit_event(audit_log_path, "live_pilot_service_started", {"mode": "loop", "iterations": iterations})

    rollup = {
        "runs": int(iterations),
        "submit_dispatch_by_reason": {},
        "submitted_signatures": 0,
        "pause_latch_events": 0,
        "pause_reset_events": 0,
        "cycles_completed": 0,
    }
    cycle_results = []
    for i in range(iterations):
        result = adapter.buy(str(token_address), str(symbol), float(entry_price), float(usd_size))
        payload = {
            "ok": bool(result.ok),
            "action": result.action,
            "message": result.message,
            "metadata": dict(result.metadata or {}),
            "iteration": i,
        }
        _audit_result_and_dispatch(audit_log_path, payload)
        dispatch = payload["metadata"].get("submit_dispatch") if isinstance(payload.get("metadata"), dict) else {}
        if isinstance(dispatch, dict):
            _rollup_update_from_dispatch(rollup, dispatch)
        append_audit_event(
            audit_log_path,
            "live_pilot_service_cycle_completed",
            {
                "iteration": i,
                "submit_dispatch_reason": (dispatch or {}).get("reason") if isinstance(dispatch, dict) else None,
                "pause_latched": bool((dispatch or {}).get("pause_latched", False)) if isinstance(dispatch, dict) else False,
            },
        )
        cycle_results.append({"iteration": i, "submit_dispatch_reason": (dispatch or {}).get("reason") if isinstance(dispatch, dict) else None})
        rollup["cycles_completed"] = int(rollup.get("cycles_completed", 0)) + 1

    append_audit_event(audit_log_path, "live_pilot_service_completed", {"rollup": rollup})
    return {"audit_log_path": audit_log_path, "rollup": rollup, "cycles": cycle_results}


def _main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--token-address", required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--entry-price", type=float, required=True)
    p.add_argument("--usd-size", type=float, required=True)
    p.add_argument("--audit-log-dir", default="data/exports")
    p.add_argument("--iterations", type=int, default=1)
    p.add_argument("--allow-unsafe-paths", action="store_true")
    p.add_argument("--adapter-config-json", default="")
    p.add_argument("--adapter-config-json-path", default="")
    args = p.parse_args()

    if not args.allow_unsafe_paths:
        ensure_dir_within_base(args.audit_log_dir)

    adapter_config = None
    if args.adapter_config_json and args.adapter_config_json_path:
        raise ValueError("provide only one of --adapter-config-json or --adapter-config-json-path")
    if args.adapter_config_json_path:
        adapter_config = json.loads(Path(args.adapter_config_json_path).read_text(encoding="utf-8"))
    elif args.adapter_config_json:
        adapter_config = json.loads(args.adapter_config_json)
    if int(args.iterations) == 1:
        out = run_live_pilot_service_once(
            token_address=args.token_address,
            symbol=args.symbol,
            entry_price=args.entry_price,
            usd_size=args.usd_size,
            audit_log_dir=args.audit_log_dir,
            adapter_config=adapter_config,
        )
    else:
        out = run_live_pilot_service_loop(
            token_address=args.token_address,
            symbol=args.symbol,
            entry_price=args.entry_price,
            usd_size=args.usd_size,
            iterations=args.iterations,
            audit_log_dir=args.audit_log_dir,
            adapter_config=adapter_config,
        )
    print(json.dumps({"audit_log_path": out["audit_log_path"], "rollup": out["rollup"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
