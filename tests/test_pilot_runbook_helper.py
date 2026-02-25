from src.live.pilot_runbook_helper import evaluate_pilot_runbook_checklist


def test_pilot_runbook_helper_passes_when_required_fields_present():
    result = evaluate_pilot_runbook_checklist(
        {
            "candidate_preset_name": "candidate_x",
            "token_allowlist": ["TOKEN1"],
            "max_order_usd_cap": 10.0,
            "pilot_hard_max_order_usd_cap": 25.0,
            "pilot_mode": True,
            "live_kill_switch": False,
            "audit_log_path": "data/exports/audit.jsonl",
            "operator_kill_switch_ack": True,
        }
    )
    assert result["ok"] is True


def test_pilot_runbook_helper_fails_missing_guardrails():
    result = evaluate_pilot_runbook_checklist(
        {
            "candidate_preset_name": "",
            "token_allowlist": [],
            "max_order_usd_cap": 100.0,
            "pilot_hard_max_order_usd_cap": 25.0,
            "pilot_mode": False,
            "live_kill_switch": True,
            "audit_log_path": "",
            "operator_kill_switch_ack": False,
        }
    )
    assert result["ok"] is False
    failed = [c["check"] for c in result["checks"] if not c["ok"]]
    assert "candidate_preset_selected" in failed
    assert "live_kill_switch_false" in failed
