from src.live import dry_run_flow_demo


def test_run_dry_run_demo_returns_structured_results(monkeypatch):
    monkeypatch.setattr(dry_run_flow_demo, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(
        dry_run_flow_demo,
        "execute_buy_with_controls",
        lambda **kwargs: {"ok": True, "risk_allowed": True, "execution": {"mock": True}, "reason": ""},
    )
    monkeypatch.setattr(
        dry_run_flow_demo,
        "execute_sell_with_retry",
        lambda **kwargs: {"ok": True, "attempts": 1, "result": "ok", "errors": []},
    )

    out = dry_run_flow_demo.run_dry_run_demo(audit_log_dir="data/exports", fail_sell_once=False)

    assert out["audit_log_path"] == "audit.jsonl"
    assert out["buy_result"]["ok"] is True
    assert out["sell_result"]["attempts"] == 1


def test_run_dry_run_demo_can_inject_fail_sell_flag(monkeypatch):
    created = {}

    class FakeAdapter:
        def __init__(self, fail_actions=None):
            created["fail_actions"] = fail_actions

    monkeypatch.setattr(dry_run_flow_demo, "DryRunExecutionAdapter", FakeAdapter)
    monkeypatch.setattr(dry_run_flow_demo, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(dry_run_flow_demo, "execute_buy_with_controls", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(dry_run_flow_demo, "execute_sell_with_retry", lambda **kwargs: {"ok": False})

    dry_run_flow_demo.run_dry_run_demo(fail_sell_once=True)

    assert created["fail_actions"] == {"sell"}


def test_run_dry_run_orchestration_loop_returns_cycles(monkeypatch):
    monkeypatch.setattr(dry_run_flow_demo, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(dry_run_flow_demo, "append_audit_event", lambda path, event_type, payload: events.append(event_type))
    monkeypatch.setattr(
        dry_run_flow_demo,
        "execute_buy_with_controls",
        lambda **kwargs: {"ok": True, "risk_allowed": True, "execution": {"mock": True}, "reason": ""},
    )
    attempts = {"n": 0}

    def fake_sell_retry(**kwargs):
        attempts["n"] += 1
        return {"ok": attempts["n"] % 2 == 1, "attempts": 2 if attempts["n"] == 1 else 1, "result": None, "errors": []}

    monkeypatch.setattr(dry_run_flow_demo, "execute_sell_with_retry", fake_sell_retry)

    out = dry_run_flow_demo.run_dry_run_orchestration_loop(iterations=3, fail_sell_attempts=0)

    assert out["iterations"] == 3
    assert len(out["cycles"]) == 3
    assert events[0] == "loop_started"
    assert events[-1] == "loop_completed"


def test_run_dry_run_orchestration_loop_rejects_invalid_iterations():
    try:
        dry_run_flow_demo.run_dry_run_orchestration_loop(iterations=0)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "iterations" in str(exc)


def test_run_candidate_preset_dry_run_loop_reads_preset_and_returns_cycles(monkeypatch):
    monkeypatch.setattr(
        dry_run_flow_demo,
        "get_candidate_preset",
        lambda preset_name, presets_path: {
            "name": "candidate_final_v1_tp_higher_034",
            "usd_size": 100.0,
            "sell_price": 0.034,
            "stop_loss_percent": 0.12,
        },
    )
    monkeypatch.setattr(dry_run_flow_demo, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(dry_run_flow_demo, "append_audit_event", lambda path, event_type, payload: events.append((event_type, payload)))
    monkeypatch.setattr(dry_run_flow_demo, "execute_buy_with_controls", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(dry_run_flow_demo, "execute_sell_with_retry", lambda **kwargs: {"ok": True, "attempts": 2})

    out = dry_run_flow_demo.run_candidate_preset_dry_run_loop(iterations=2, fail_sell_attempts=1)

    assert out["preset"]["name"] == "candidate_final_v1_tp_higher_034"
    assert out["iterations"] == 2
    assert len(out["cycles"]) == 2
    assert out["cycles"][0]["sell_price"] == 0.034
    assert events[0][0] == "candidate_loop_started"
    assert events[-1][0] == "candidate_loop_completed"


def test_toggle_fail_sell_adapter_fails_then_succeeds():
    adapter = dry_run_flow_demo.ToggleFailSellAdapter(fail_sell_attempts=2)
    r1 = adapter.sell(1, 0.02)
    r2 = adapter.sell(1, 0.02)
    r3 = adapter.sell(1, 0.02)

    assert r1.ok is False
    assert r2.ok is False
    assert r3.ok is True
