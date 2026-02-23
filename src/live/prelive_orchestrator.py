from dataclasses import asdict
from typing import Any

from src.live.audit_logger import append_audit_event
from src.live.interfaces import ExecutionAdapter, ExecutionResult, RiskDecision, RiskEngine
from src.live.retry_simulator import retry_operation


def execute_buy_with_controls(
    adapter: ExecutionAdapter,
    risk_engine: RiskEngine,
    audit_log_path: str | None,
    token_address: str,
    symbol: str,
    entry_price: float,
    usd_size: float,
) -> dict[str, Any]:
    """
    Pre-live orchestration skeleton for a buy path:
    - ask risk engine
    - attempt adapter buy
    - audit decision + result
    """
    risk = risk_engine.can_buy(token_address=token_address, symbol=symbol, usd_size=usd_size)
    if audit_log_path:
        append_audit_event(audit_log_path, "risk_decision", {"action": "buy", **asdict(risk)})

    if not risk.allowed:
        return {"ok": False, "risk_allowed": False, "execution": None, "reason": risk.reason}

    result = adapter.buy(token_address=token_address, symbol=symbol, entry_price=entry_price, usd_size=usd_size)
    if audit_log_path:
        append_audit_event(
            audit_log_path,
            "execution_result",
            {"action": "buy", **asdict(result)},
        )
    return {"ok": result.ok, "risk_allowed": True, "execution": result, "reason": result.message}


def execute_sell_with_retry(
    adapter: ExecutionAdapter,
    audit_log_path: str | None,
    position_id: int,
    exit_price: float,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """
    Pre-live orchestration skeleton for a sell path using structured retry metadata.
    """

    def operation() -> ExecutionResult:
        result = adapter.sell(position_id=position_id, exit_price=exit_price)
        if not result.ok:
            raise RuntimeError(result.message or "sell failed")
        return result

    retry_result = retry_operation(operation, max_attempts=max_attempts)
    if audit_log_path:
        append_audit_event(
            audit_log_path,
            "sell_retry_result",
            {
                "position_id": position_id,
                "exit_price": exit_price,
                "ok": retry_result["ok"],
                "attempts": retry_result["attempts"],
                "errors": retry_result["errors"],
            },
        )
    return retry_result


class AllowAllRiskEngine(RiskEngine):
    def can_buy(self, token_address: str, symbol: str, usd_size: float) -> RiskDecision:
        return RiskDecision(allowed=True, reason="")


class BlockAllRiskEngine(RiskEngine):
    def can_buy(self, token_address: str, symbol: str, usd_size: float) -> RiskDecision:
        return RiskDecision(allowed=False, reason="blocked by pre-live stub")
