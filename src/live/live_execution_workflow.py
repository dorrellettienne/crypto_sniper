from hashlib import sha256
from typing import Any

from src.live.confirmation_retry_policy import (
    ConfirmationRetryPolicy,
)
from src.live.confirmation_reconciliation import reconcile_confirmation_attempts
from src.live.idempotency import build_client_order_id, build_request_fingerprint
from src.live.order_lifecycle import (
    make_submitted,
)


def _hash_id(prefix: str, raw: str) -> str:
    return f"{prefix}_{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def build_workflow_identifiers(
    action: str,
    token_address: str | None = None,
    symbol: str | None = None,
    entry_price: float | None = None,
    usd_size: float | None = None,
    position_id: int | None = None,
    exit_price: float | None = None,
    stop_percent: float | None = None,
    sequence: int = 1,
) -> dict[str, str]:
    action = str(action)
    if action == "buy":
        ta = str(token_address or "")
        sym = str(symbol or "")
        ep = float(entry_price if entry_price is not None else 0.0)
        size = float(usd_size if usd_size is not None else 0.0)
        return {
            "client_order_id": build_client_order_id(action, ta, sym, ep, size, sequence=sequence),
            "request_fingerprint": build_request_fingerprint(action, ta, sym, ep, size),
        }

    raw = "|".join(
        [
            action,
            str(position_id if position_id is not None else ""),
            str(exit_price if exit_price is not None else ""),
            str(stop_percent if stop_percent is not None else ""),
            str(sequence),
        ]
    )
    return {
        "client_order_id": _hash_id("coid", raw),
        "request_fingerprint": _hash_id("req", raw),
    }


def build_execution_preview_workflow(
    *,
    action: str,
    order_preview: dict[str, Any],
    rpc_health: dict[str, Any],
    client_order_id: str,
    request_fingerprint: str,
    policy: ConfirmationRetryPolicy | None = None,
    elapsed_seconds: float = 0.0,
    simulated_outcome: str = "retry",
    confirmation_outcomes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Build audit/lifecycle-friendly skeleton execution workflow metadata.
    No network calls and no real order submission.
    """
    policy = policy or ConfirmationRetryPolicy()
    simulated_outcome = str(simulated_outcome)

    submitted = make_submitted(
        action=action,
        order_id=client_order_id,
        attempt=1,
        message="skeleton preview created",
        metadata={
            "request_fingerprint": request_fingerprint,
            "order_preview": dict(order_preview or {}),
            "rpc_health": dict(rpc_health or {}),
        },
    )
    if confirmation_outcomes is None:
        if simulated_outcome == "retry":
            confirmation_outcomes = ["pending"]
        elif simulated_outcome == "timeout":
            confirmation_outcomes = ["pending"]
            elapsed_seconds = max(elapsed_seconds, policy.timeout_seconds)
        elif simulated_outcome == "fail":
            confirmation_outcomes = ["error"]
        elif simulated_outcome == "confirmed":
            confirmation_outcomes = ["confirmed"]
        else:
            raise ValueError("simulated_outcome must be one of: retry, fail, timeout, confirmed")

    reconciliation = reconcile_confirmation_attempts(
        submitted,
        policy=policy,
        outcomes=confirmation_outcomes,
        elapsed_seconds_by_attempt=[elapsed_seconds],
    )
    retry_policy_decision = (
        dict(reconciliation.retry_decisions[-1])
        if reconciliation.retry_decisions
        else {"decision": "confirmed", "next_attempt": None, "backoff_seconds": 0.0, "reason": "confirmed"}
    )
    final_decision = reconciliation.final_decision

    return {
        "client_order_id": client_order_id,
        "request_fingerprint": request_fingerprint,
        "order_preview": dict(order_preview or {}),
        "rpc_health": dict(rpc_health or {}),
        "retry_policy": {
            "max_attempts": policy.max_attempts,
            "timeout_seconds": policy.timeout_seconds,
            "backoff_mode": policy.backoff_mode,
            "backoff_base_seconds": policy.backoff_base_seconds,
        },
        "retry_policy_decision": dict(retry_policy_decision),
        "final_decision": final_decision,
        "lifecycle_events": list(reconciliation.lifecycle_events),
        "reconciliation": {
            "final_status": reconciliation.final_status,
            "attempts_used": reconciliation.attempts_used,
            "elapsed_seconds": reconciliation.elapsed_seconds,
            "retry_decisions": list(reconciliation.retry_decisions),
            "final_decision": reconciliation.final_decision,
        },
    }
