from src.live.live_safety_gate import evaluate_live_safety_gate


def test_live_safety_gate_allows_when_live_disabled():
    decision = evaluate_live_safety_gate({"live_enabled": False})
    assert decision.allowed is True
    assert decision.reason == "live_disabled"


def test_live_safety_gate_blocks_kill_switch():
    decision = evaluate_live_safety_gate({"live_enabled": True, "live_kill_switch": True})
    assert decision.allowed is False
    assert decision.reason == "live_kill_switch_active"


def test_live_safety_gate_blocks_missing_allowlist():
    decision = evaluate_live_safety_gate({"live_enabled": True, "live_kill_switch": False})
    assert decision.allowed is False
    assert decision.reason == "missing_allowlist"


def test_live_safety_gate_blocks_missing_or_invalid_cap():
    missing = evaluate_live_safety_gate({"live_enabled": True, "allowlist_tokens": ["TOKEN_A"]})
    invalid = evaluate_live_safety_gate({"live_enabled": True, "allowlist_tokens": ["TOKEN_A"], "max_order_usd_cap": 0})
    assert missing.reason == "missing_max_order_usd_cap"
    assert invalid.reason == "invalid_max_order_usd_cap"


def test_live_safety_gate_blocks_cap_above_hard_cap():
    decision = evaluate_live_safety_gate(
        {
            "live_enabled": True,
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 5000,
            "hard_max_order_usd_cap": 1000,
        }
    )
    assert decision.allowed is False
    assert decision.reason == "max_order_usd_cap_exceeds_hard_cap"


def test_live_safety_gate_allows_valid_live_startup():
    decision = evaluate_live_safety_gate(
        {
            "live_enabled": True,
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "hard_max_order_usd_cap": 1000,
        }
    )
    assert decision.allowed is True
    assert decision.reason == ""
