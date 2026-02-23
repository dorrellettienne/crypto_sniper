from src.live.interfaces import ExecutionResult, RiskDecision
from src.live.prelive_orchestrator import (
    AllowAllRiskEngine,
    BlockAllRiskEngine,
    execute_buy_with_controls,
    execute_sell_with_retry,
)


class _Adapter:
    def __init__(self):
        self.sell_calls = 0

    def buy(self, token_address: str, symbol: str, entry_price: float, usd_size: float):
        return ExecutionResult(ok=True, action="buy", position_id=7, message="ok")

    def sell(self, position_id: int, exit_price: float):
        self.sell_calls += 1
        if self.sell_calls == 1:
            return ExecutionResult(ok=False, action="sell", position_id=position_id, message="temp")
        return ExecutionResult(ok=True, action="sell", position_id=position_id, pnl=10.0, message="ok")

    def stop_loss(self, position_id: int, stop_percent: float):
        return ExecutionResult(ok=True, action="stop_loss", position_id=position_id, pnl=-1.0)


def test_execute_buy_with_controls_allows_and_executes(monkeypatch):
    events = []
    monkeypatch.setattr("src.live.prelive_orchestrator.append_audit_event", lambda *args, **kwargs: events.append(args[1]))
    out = execute_buy_with_controls(
        adapter=_Adapter(),
        risk_engine=AllowAllRiskEngine(),
        audit_log_path="audit.jsonl",
        token_address="T",
        symbol="S",
        entry_price=0.01,
        usd_size=100.0,
    )
    assert out["ok"] is True
    assert out["risk_allowed"] is True
    assert out["execution"].position_id == 7
    assert events == ["risk_decision", "execution_result"]


def test_execute_buy_with_controls_blocks(monkeypatch):
    events = []
    monkeypatch.setattr("src.live.prelive_orchestrator.append_audit_event", lambda *args, **kwargs: events.append(args[1]))
    out = execute_buy_with_controls(
        adapter=_Adapter(),
        risk_engine=BlockAllRiskEngine(),
        audit_log_path="audit.jsonl",
        token_address="T",
        symbol="S",
        entry_price=0.01,
        usd_size=100.0,
    )
    assert out["ok"] is False
    assert out["risk_allowed"] is False
    assert out["execution"] is None
    assert events == ["risk_decision"]


def test_execute_sell_with_retry_retries_and_logs(monkeypatch):
    events = []
    monkeypatch.setattr("src.live.prelive_orchestrator.append_audit_event", lambda *args, **kwargs: events.append(args[1]))
    adapter = _Adapter()
    out = execute_sell_with_retry(adapter=adapter, audit_log_path="audit.jsonl", position_id=1, exit_price=0.02, max_attempts=3)
    assert out["ok"] is True
    assert out["attempts"] == 2
    assert events == ["sell_retry_result"]


def test_execute_sell_with_retry_returns_failure_after_max_attempts(monkeypatch):
    events = []
    monkeypatch.setattr("src.live.prelive_orchestrator.append_audit_event", lambda *args, **kwargs: events.append(args[1]))

    class FailAdapter(_Adapter):
        def sell(self, position_id: int, exit_price: float):
            self.sell_calls += 1
            return ExecutionResult(ok=False, action="sell", position_id=position_id, message="still failing")

    out = execute_sell_with_retry(adapter=FailAdapter(), audit_log_path="audit.jsonl", position_id=1, exit_price=0.02, max_attempts=2)

    assert out["ok"] is False
    assert out["attempts"] == 2
    assert out["errors"] == ["still failing", "still failing"]
    assert events == ["sell_retry_result"]
