from src.live.live_send_pause_reset_helper import evaluate_live_send_pause_reset


def test_live_send_pause_reset_helper_approves_matching_tokens():
    out = evaluate_live_send_pause_reset(required_token="ABC", provided_token="ABC")
    assert out["ok"] is True
    assert out["reason"] == "reset_approved"
    assert out["adapter_config_patch"]["live_send_pause_reset_required_token"] == "ABC"


def test_live_send_pause_reset_helper_rejects_missing_or_mismatch():
    assert evaluate_live_send_pause_reset(required_token="", provided_token="X")["reason"] == "missing_required_token"
    assert evaluate_live_send_pause_reset(required_token="X", provided_token="")["reason"] == "missing_provided_token"
    assert evaluate_live_send_pause_reset(required_token="X", provided_token="Y")["reason"] == "token_mismatch"

