from src.live.live_pilot_safety import (
    build_live_startup_guardrails,
    evaluate_live_pilot_safety_gate,
)


def test_live_pilot_safety_allows_when_pilot_mode_disabled():
    decision = evaluate_live_pilot_safety_gate({"live_enabled": True, "pilot_mode": False})
    assert decision.allowed is True
    assert decision.reason == "pilot_mode_disabled"


def test_live_pilot_safety_blocks_missing_audit_log_path():
    decision = evaluate_live_pilot_safety_gate(
        {
            "live_enabled": True,
            "pilot_mode": True,
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 10,
        }
    )
    assert decision.allowed is False
    assert decision.reason == "missing_audit_log_path"


def test_live_pilot_safety_blocks_cap_above_pilot_hard_cap():
    decision = evaluate_live_pilot_safety_gate(
        {
            "live_enabled": True,
            "pilot_mode": True,
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 50,
            "pilot_hard_max_order_usd_cap": 25,
            "audit_log_path": "data/exports/audit.jsonl",
        }
    )
    assert decision.allowed is False
    assert decision.reason == "pilot_max_order_usd_cap_exceeds_pilot_hard_cap"


def test_live_pilot_safety_blocks_when_single_position_required_but_not_one():
    decision = evaluate_live_pilot_safety_gate(
        {
            "live_enabled": True,
            "pilot_mode": True,
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 10,
            "audit_log_path": "data/exports/audit.jsonl",
            "pilot_require_single_position": True,
            "max_concurrent_positions": 2,
        }
    )
    assert decision.allowed is False
    assert decision.reason == "pilot_requires_single_position_limit"


def test_live_pilot_safety_allows_valid_pilot_config():
    decision = evaluate_live_pilot_safety_gate(
        {
            "live_enabled": True,
            "pilot_mode": True,
            "allowlist_tokens": ["TOKEN_A", "TOKEN_B"],
            "max_order_usd_cap": 10,
            "pilot_hard_max_order_usd_cap": 25,
            "audit_log_path": "data/exports/audit.jsonl",
            "candidate_preset_name": "candidate_final",
            "pilot_require_single_position": True,
            "max_concurrent_positions": 1,
        }
    )
    assert decision.allowed is True
    assert decision.details["mode"] == "pilot_live"
    assert decision.details["allowlist_count"] == 2


def test_build_live_startup_guardrails_returns_clear_mode_metadata():
    meta = build_live_startup_guardrails(
        {
            "live_enabled": True,
            "pilot_mode": True,
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 10,
            "audit_log_path": "data/exports/audit.jsonl",
            "candidate_preset_name": "candidate_x",
        }
    )
    assert meta["mode"] == "pilot_live"
    assert meta["candidate_preset_name"] == "candidate_x"
    assert meta["allowlist_count"] == 1

