import json
from pathlib import Path
from typing import Any


PRELIVE_OPS_PRESETS: dict[str, dict[str, Any]] = {
    "strict_pilot": {
        "use_stub_signals": False,
        "use_dexscreener_signals": True,
        "rollup_emit_every": 1,
        "enable_mechanical_safety_filter": True,
        "use_mechanical_safety_profile": True,
        "mechanical_safety_profile_name": "strict",
        "enable_volatility_guard": True,
        "volatility_max_loss_streak_block": 2,
        "volatility_loss_streak_derisk_threshold": 1,
        "volatility_max_session_drawdown_usd_block": 50.0,
        "volatility_session_drawdown_derisk_threshold_usd": 25.0,
        "volatility_derisk_size_multiplier": 0.5,
    },
    "paper_shadow": {
        "use_stub_signals": False,
        "use_dexscreener_signals": True,
        "rollup_emit_every": 5,
        "enable_mechanical_safety_filter": True,
        "use_mechanical_safety_profile": True,
        "mechanical_safety_profile_name": "pilot",
        "enable_volatility_guard": True,
        "volatility_max_loss_streak_block": 3,
        "volatility_loss_streak_derisk_threshold": 2,
        "volatility_derisk_size_multiplier": 0.7,
    },
    "relaxed_research": {
        "use_stub_signals": False,
        "use_dexscreener_signals": True,
        "rollup_emit_every": 10,
        "enable_mechanical_safety_filter": True,
        "use_mechanical_safety_profile": True,
        "mechanical_safety_profile_name": "relaxed",
        "enable_volatility_guard": False,
    },
}


def apply_prelive_ops_preset(args) -> tuple[Any, dict[str, Any] | None]:
    preset_name = str(getattr(args, "ops_preset", "") or "").strip()
    if not preset_name:
        return args, None
    if preset_name not in PRELIVE_OPS_PRESETS:
        raise ValueError(f"unknown ops preset: {preset_name}")
    preset = dict(PRELIVE_OPS_PRESETS[preset_name])
    for key, value in preset.items():
        setattr(args, key, value)
    return args, {"name": preset_name, "values": preset}


def build_effective_prelive_config_summary(args) -> dict[str, Any]:
    signal_mode = (
        "file"
        if bool(getattr(args, "signals_file_path", ""))
        else "dexscreener"
        if bool(getattr(args, "use_dexscreener_signals", False))
        else "stub"
        if bool(getattr(args, "use_stub_signals", False))
        else "none"
    )
    return {
        "ops_preset": str(getattr(args, "ops_preset", "") or "") or None,
        "signal_mode": signal_mode,
        "max_iterations": None if bool(getattr(args, "continuous", False)) else getattr(args, "max_iterations", None),
        "audit_log_dir": getattr(args, "audit_log_dir", None),
        "rollup_export_json_dir": getattr(args, "rollup_export_json_dir", None),
        "rollup_export_csv_dir": getattr(args, "rollup_export_csv_dir", None),
        "safety": {
            "use_token_safety_profile": bool(getattr(args, "use_token_safety_profile", False)),
            "token_safety_profile_name": getattr(args, "token_safety_profile_name", None),
            "safety_min_liquidity_usd": getattr(args, "safety_min_liquidity_usd", None),
        },
        "mechanical": {
            "enabled": bool(getattr(args, "enable_mechanical_safety_filter", False)),
            "use_profile": bool(getattr(args, "use_mechanical_safety_profile", False)),
            "profile_name": getattr(args, "mechanical_safety_profile_name", None),
            "rpc_url_configured": bool(str(getattr(args, "mechanical_rpc_url", "") or "").strip()),
            "quote_url_configured": bool(str(getattr(args, "mechanical_quote_url", "") or "").strip()),
            "max_buy_price_impact_pct": getattr(args, "mechanical_max_buy_price_impact_pct", None),
            "min_buy_liquidity_usd": getattr(args, "mechanical_min_buy_liquidity_usd", None),
            "max_quote_age_ms": getattr(args, "mechanical_max_quote_age_ms", None),
        },
        "volatility_guard": {
            "enabled": bool(getattr(args, "enable_volatility_guard", False)),
            "max_loss_streak_block": getattr(args, "volatility_max_loss_streak_block", None),
            "loss_streak_derisk_threshold": getattr(args, "volatility_loss_streak_derisk_threshold", None),
            "max_session_drawdown_usd_block": getattr(args, "volatility_max_session_drawdown_usd_block", None),
            "session_drawdown_derisk_threshold_usd": getattr(args, "volatility_session_drawdown_derisk_threshold_usd", None),
            "derisk_size_multiplier": getattr(args, "volatility_derisk_size_multiplier", None),
        },
    }


def validate_prelive_preflight(args) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    selected_signal_sources = int(bool(getattr(args, "use_stub_signals", False))) + int(
        bool(getattr(args, "signals_file_path", ""))
    ) + int(bool(getattr(args, "use_dexscreener_signals", False)))
    if selected_signal_sources > 1:
        errors.append("multiple_signal_sources_selected")

    if bool(getattr(args, "enable_mechanical_safety_filter", False)):
        if not str(getattr(args, "mechanical_quote_url", "") or "").strip():
            warnings.append("mechanical_quote_url_missing")
        if not str(getattr(args, "mechanical_rpc_url", "") or "").strip():
            warnings.append("mechanical_rpc_url_missing")
        if bool(getattr(args, "mechanical_require_sell_route", False)) and not str(getattr(args, "mechanical_quote_url", "") or "").strip():
            errors.append("sell_route_check_requires_mechanical_quote_url")

    if bool(getattr(args, "enable_volatility_guard", False)):
        multiplier = float(getattr(args, "volatility_derisk_size_multiplier", 1.0))
        if multiplier <= 0:
            errors.append("invalid_volatility_derisk_size_multiplier")
        if getattr(args, "volatility_max_loss_streak_block", None) is None and getattr(
            args, "volatility_max_session_drawdown_usd_block", None
        ) is None:
            warnings.append("volatility_guard_enabled_without_block_threshold")

    audit_dir = str(getattr(args, "audit_log_dir", "") or "").strip()
    if not audit_dir:
        errors.append("missing_audit_log_dir")

    if bool(getattr(args, "use_dexscreener_signals", False)) and not str(getattr(args, "dexscreener_fetch_url", "") or "").strip():
        warnings.append("dexscreener_fetch_url_missing_using_stub_empty_fetcher")

    for path_attr in ["audit_log_dir", "rollup_export_json_dir", "rollup_export_csv_dir"]:
        value = getattr(args, path_attr, None)
        if value:
            try:
                Path(str(value))
            except Exception:
                errors.append(f"invalid_path_{path_attr}")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "effective_config_summary": build_effective_prelive_config_summary(args),
    }


def load_audit_events_from_jsonl(path: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def build_session_incident_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    mechanical_reasons: dict[str, int] = {}
    volatility_reasons: dict[str, int] = {}
    counters = {
        "total_events": len(events),
        "service_errors": 0,
        "signal_fetch_retry_events": 0,
        "signal_fetch_errors": 0,
        "signal_payload_stale_events": 0,
        "mechanical_blocks": 0,
        "quote_stale_blocks": 0,
        "volatility_guard_blocks": 0,
        "volatility_guard_derisks": 0,
        "quote_retry_events": 0,
        "rpc_retry_events": 0,
        "buy_ok": 0,
        "buy_failed": 0,
        "sell_ok": 0,
        "sell_failed": 0,
        "execution_partial_fills": 0,
        "execution_stale_quote_rejects": 0,
        "execution_slippage_rejects": 0,
    }
    last_rollup = None
    for evt in events:
        et = str(evt.get("event_type") or "")
        payload = evt.get("payload") or {}
        if et == "service_error":
            counters["service_errors"] += 1
        if et == "signal_source_transport_status":
            counters["signal_fetch_retry_events"] += int(payload.get("fetch_retry_events", 0) or 0)
            counters["signal_fetch_errors"] += int(payload.get("fetch_transport_errors", 0) or 0)
            counters["signal_payload_stale_events"] += int(payload.get("fetch_stale_payload_events", 0) or 0)
        if et == "mechanical_safety_decision":
            if payload.get("allowed") is False:
                counters["mechanical_blocks"] += 1
                reason = str(payload.get("reason") or "unknown")
                mechanical_reasons[reason] = mechanical_reasons.get(reason, 0) + 1
                if reason == "quote_stale_or_invalid":
                    counters["quote_stale_blocks"] += 1
            details = payload.get("details") or {}
            telemetry = details.get("telemetry") if isinstance(details, dict) else {}
            if isinstance(telemetry, dict):
                counters["quote_retry_events"] += int(telemetry.get("quote_retry_events", 0) or 0)
                counters["rpc_retry_events"] += int(telemetry.get("rpc_retry_events", 0) or 0)
        if et == "volatility_guard_decision":
            if payload.get("allowed") is False:
                counters["volatility_guard_blocks"] += 1
                reason = str(payload.get("reason") or "unknown")
                volatility_reasons[reason] = volatility_reasons.get(reason, 0) + 1
            if payload.get("derisk_applied"):
                counters["volatility_guard_derisks"] += 1
        if et == "execution_result":
            if str(payload.get("action") or "") == "buy":
                md = payload.get("metadata") or {}
                outcome = str(md.get("execution_outcome_class") or "")
                if outcome == "partial_fill":
                    counters["execution_partial_fills"] += 1
                elif outcome == "stale_quote_reject":
                    counters["execution_stale_quote_rejects"] += 1
                elif outcome == "slippage_tolerance_exceeded":
                    counters["execution_slippage_rejects"] += 1
        if et == "service_cycle_completed":
            status = str(payload.get("status") or "")
            if status == "ok":
                counters["buy_ok"] += 1
                counters["sell_ok"] += 1
            elif status == "buy_failed":
                counters["buy_failed"] += 1
            elif status == "sell_failed":
                counters["sell_failed"] += 1
        if et in {"service_rollup", "service_completed"}:
            last_rollup = payload

    def top_reason(d: dict[str, int]) -> dict[str, Any] | None:
        if not d:
            return None
        key, count = sorted(d.items(), key=lambda kv: (-int(kv[1]), kv[0]))[0]
        return {"reason": key, "count": int(count)}

    return {
        "report_type": "prelive_session_incident_report_v1",
        "counters": counters,
        "top_mechanical_reject": top_reason(mechanical_reasons),
        "top_volatility_guard_block": top_reason(volatility_reasons),
        "mechanical_reject_counts": dict(sorted(mechanical_reasons.items())),
        "volatility_guard_block_counts": dict(sorted(volatility_reasons.items())),
        "last_rollup": last_rollup,
    }


def save_session_incident_report_json(report: dict[str, Any], output_path: str) -> str:
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    return str(p)
