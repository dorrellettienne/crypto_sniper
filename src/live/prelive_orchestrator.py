from dataclasses import asdict
from typing import Any

from src.live.audit_logger import append_audit_event
from src.live.interfaces import ExecutionAdapter, ExecutionResult, RiskDecision, RiskEngine
from src.live.order_lifecycle import (
    lifecycle_event_to_dict,
    make_submitted,
    mark_confirmed,
    mark_failed,
    mark_retrying,
)
from src.live.retry_simulator import retry_operation


def execute_buy_with_controls(
    adapter: ExecutionAdapter,
    risk_engine: RiskEngine,
    audit_log_path: str | None,
    token_address: str,
    symbol: str,
    entry_price: float,
    usd_size: float,
    client_order_id: str | None = None,
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
        if audit_log_path and client_order_id:
            lifecycle = mark_failed(
                make_submitted(
                    action="buy",
                    order_id=client_order_id,
                    metadata={"token_address": token_address, "symbol": symbol, "entry_price": entry_price, "usd_size": usd_size},
                ),
                message=f"risk blocked: {risk.reason}",
                metadata={"risk_allowed": False},
            )
            append_audit_event(audit_log_path, "order_lifecycle_event", lifecycle_event_to_dict(lifecycle))
        return {"ok": False, "risk_allowed": False, "execution": None, "reason": risk.reason}

    result = adapter.buy(token_address=token_address, symbol=symbol, entry_price=entry_price, usd_size=usd_size)
    if audit_log_path:
        append_audit_event(
            audit_log_path,
            "execution_result",
            {"action": "buy", **asdict(result)},
        )
        if client_order_id:
            lifecycle = make_submitted(
                action="buy",
                order_id=client_order_id,
                metadata={"token_address": token_address, "symbol": symbol, "entry_price": entry_price, "usd_size": usd_size},
            )
            lifecycle = mark_confirmed(lifecycle, message=result.message, metadata={"ok": result.ok, "position_id": result.position_id}) if result.ok else mark_failed(
                lifecycle,
                message=result.message,
                metadata={"ok": result.ok},
            )
            append_audit_event(audit_log_path, "order_lifecycle_event", lifecycle_event_to_dict(lifecycle))
    return {"ok": result.ok, "risk_allowed": True, "execution": result, "reason": result.message}


def execute_sell_with_retry(
    adapter: ExecutionAdapter,
    audit_log_path: str | None,
    position_id: int,
    exit_price: float,
    max_attempts: int = 3,
    client_order_id: str | None = None,
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
        if client_order_id:
            lifecycle = make_submitted(
                action="sell",
                order_id=client_order_id,
                metadata={"position_id": position_id, "exit_price": exit_price},
            )
            for idx in range(2, int(retry_result["attempts"]) + 1):
                lifecycle = mark_retrying(lifecycle, metadata={"previous_error": (retry_result["errors"][idx - 2] if len(retry_result["errors"]) >= idx - 1 else "")})
                append_audit_event(audit_log_path, "order_lifecycle_event", lifecycle_event_to_dict(lifecycle))
            if retry_result["ok"]:
                lifecycle = mark_confirmed(
                    lifecycle,
                    metadata={"attempts": retry_result["attempts"]},
                )
            else:
                lifecycle = mark_failed(
                    lifecycle,
                    message=(retry_result["errors"][-1] if retry_result["errors"] else "sell failed"),
                    metadata={"attempts": retry_result["attempts"]},
                )
            append_audit_event(audit_log_path, "order_lifecycle_event", lifecycle_event_to_dict(lifecycle))
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
