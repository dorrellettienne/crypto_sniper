import json

from src.live import prelive_ops


def _args(**overrides):
    class A:
        pass

    a = A()
    defaults = {
        "ops_preset": "",
        "continuous": False,
        "max_iterations": 10,
        "audit_log_dir": "data/exports",
        "rollup_export_json_dir": None,
        "rollup_export_csv_dir": None,
        "use_stub_signals": False,
        "signals_file_path": "",
        "use_dexscreener_signals": True,
        "use_token_safety_profile": False,
        "token_safety_profile_name": "strict_launch_filter",
        "safety_min_liquidity_usd": None,
        "enable_mechanical_safety_filter": False,
        "use_mechanical_safety_profile": False,
        "mechanical_safety_profile_name": "strict",
        "mechanical_rpc_url": "",
        "mechanical_quote_url": "",
        "mechanical_max_buy_price_impact_pct": None,
        "mechanical_min_buy_liquidity_usd": None,
        "mechanical_max_quote_age_ms": None,
        "mechanical_require_sell_route": False,
        "enable_volatility_guard": False,
        "volatility_max_loss_streak_block": None,
        "volatility_loss_streak_derisk_threshold": None,
        "volatility_max_session_drawdown_usd_block": None,
        "volatility_session_drawdown_derisk_threshold_usd": None,
        "volatility_derisk_size_multiplier": 1.0,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(a, k, v)
    return a


def test_apply_prelive_ops_preset_sets_expected_flags():
    args = _args(ops_preset="strict_pilot")
    args, applied = prelive_ops.apply_prelive_ops_preset(args)
    assert applied["name"] == "strict_pilot"
    assert args.enable_mechanical_safety_filter is True
    assert args.enable_volatility_guard is True


def test_validate_prelive_preflight_reports_errors_and_warnings():
    args = _args(
        use_stub_signals=True,
        use_dexscreener_signals=True,
        enable_mechanical_safety_filter=True,
        mechanical_require_sell_route=True,
        mechanical_quote_url="",
    )
    out = prelive_ops.validate_prelive_preflight(args)
    assert out["ok"] is False
    assert "multiple_signal_sources_selected" in out["errors"]
    assert "sell_route_check_requires_mechanical_quote_url" in out["errors"]
    assert "mechanical_rpc_url_missing" in out["warnings"]


def test_build_session_incident_report_summarizes_reasons_and_counters(tmp_path):
    events = [
        {"event_type": "mechanical_safety_decision", "payload": {"allowed": False, "reason": "no_buy_route", "details": {"telemetry": {"quote_retry_events": 1}}}},
        {"event_type": "mechanical_safety_decision", "payload": {"allowed": False, "reason": "quote_stale_or_invalid", "details": {"telemetry": {"quote_retry_events": 2}}}},
        {"event_type": "volatility_guard_decision", "payload": {"allowed": False, "reason": "loss_streak_circuit_breaker", "derisk_applied": False}},
        {"event_type": "volatility_guard_decision", "payload": {"allowed": True, "reason": "derisk_applied", "derisk_applied": True}},
        {"event_type": "service_error", "payload": {"error": "boom"}},
        {"event_type": "service_completed", "payload": {"rollup": {"mechanical_blocked": 2}}},
    ]
    report = prelive_ops.build_session_incident_report(events)
    assert report["counters"]["mechanical_blocks"] == 2
    assert report["counters"]["quote_stale_blocks"] == 1
    assert report["counters"]["volatility_guard_blocks"] == 1
    assert report["top_mechanical_reject"]["reason"] in {"no_buy_route", "quote_stale_or_invalid"}

    out = tmp_path / "incident.json"
    prelive_ops.save_session_incident_report_json(report, str(out))
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["report_type"] == "prelive_session_incident_report_v1"
