from src.live.interfaces import TradeSignal
from src.live.signal_provider_stub import StubSignalProvider
from src.live import prelive_service


def test_prelive_service_no_signals_rollup(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda path, event_type, payload: events.append(event_type))

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([]),
        max_iterations=3,
        audit_log_dir="data/exports",
    )

    assert out["rollup"]["iterations"] == 3
    assert out["rollup"]["signals_seen"] == 0
    assert out["rollup"]["signals_missing"] == 3
    assert events[0] == "service_started"
    assert events[-1] == "service_completed"


def test_prelive_service_risk_blocked_path(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)

    class BlockRisk:
        def can_buy(self, token_address: str, symbol: str, usd_size: float):
            from src.live.interfaces import RiskDecision
            return RiskDecision(allowed=False, reason="blocked")

    monkeypatch.setattr(prelive_service, "PreLiveRiskEngine", lambda: BlockRisk())

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="S", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
    )

    assert out["rollup"]["signals_seen"] == 1
    assert out["rollup"]["risk_blocked"] == 1
    assert out["rollup"]["buy_ok"] == 0


def test_prelive_service_allowed_execution_and_retry_path(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)

    class AllowRisk:
        def can_buy(self, token_address: str, symbol: str, usd_size: float):
            from src.live.interfaces import RiskDecision
            return RiskDecision(allowed=True, reason="")

    monkeypatch.setattr(prelive_service, "PreLiveRiskEngine", lambda: AllowRisk())
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 7})(), "reason": ""},
    )
    monkeypatch.setattr(
        prelive_service,
        "execute_sell_with_retry",
        lambda **kwargs: {"ok": True, "attempts": 2, "result": None, "errors": ["temp"]},
    )

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="S", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
    )

    assert out["rollup"]["risk_allowed"] == 1
    assert out["rollup"]["buy_ok"] == 1
    assert out["rollup"]["sell_ok"] == 1
    assert out["rollup"]["sell_retry_events"] == 1
    assert out["rollup"]["max_sell_attempts"] == 2


def test_prelive_service_rejects_invalid_iterations():
    try:
        prelive_service.run_prelive_service_loop(signal_provider=StubSignalProvider([]), max_iterations=0)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "max_iterations" in str(exc)
