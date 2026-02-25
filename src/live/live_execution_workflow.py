from hashlib import sha256
from typing import Any

from src.live.confirmation_retry_policy import (
    ConfirmationRetryPolicy,
)
from src.live.confirmation_reconciliation import (
    reconcile_confirmation_attempts,
    reconcile_live_chain_confirmation,
)
from src.live.idempotency import build_client_order_id, build_request_fingerprint
from src.live.order_lifecycle import (
    lifecycle_event_to_dict,
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
    confirmation_elapsed_seconds_by_attempt: list[float] | None = None,
    quote_age_ms_at_submit: int | None = None,
    max_quote_age_ms_before_submit: int | None = None,
    simulated_submit_latency_ms: int = 0,
    signature_status_payload: dict[str, Any] | None = None,
    tx_payload: dict[str, Any] | None = None,
    preview_estimates: dict[str, Any] | None = None,
    chain_reconciliation_thresholds: dict[str, Any] | None = None,
    chain_reconciliation_owner_filter: str | None = None,
) -> dict[str, Any]:
    """
    Build audit/lifecycle-friendly skeleton execution workflow metadata.
    No network calls and no real order submission.
    """
    policy = policy or ConfirmationRetryPolicy()
    simulated_outcome = str(simulated_outcome)
    simulated_submit_latency_ms = int(simulated_submit_latency_ms)
    if simulated_submit_latency_ms < 0:
        raise ValueError("simulated_submit_latency_ms must be >= 0")
    if quote_age_ms_at_submit is not None:
        quote_age_ms_at_submit = int(quote_age_ms_at_submit)
        if quote_age_ms_at_submit < 0:
            raise ValueError("quote_age_ms_at_submit must be >= 0")
    if max_quote_age_ms_before_submit is not None:
        max_quote_age_ms_before_submit = int(max_quote_age_ms_before_submit)
        if max_quote_age_ms_before_submit < 0:
            raise ValueError("max_quote_age_ms_before_submit must be >= 0")

    stale_submit_reject = (
        quote_age_ms_at_submit is not None
        and max_quote_age_ms_before_submit is not None
        and quote_age_ms_at_submit > max_quote_age_ms_before_submit
    )

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
    if stale_submit_reject:
        confirmation_outcomes = []
    elif confirmation_outcomes is None:
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

    if stale_submit_reject:
        reconciliation = {
            "final_status": "stale_quote_reject",
            "attempts_used": 0,
            "elapsed_seconds": 0.0,
            "retry_decisions": [],
            "lifecycle_events": [submitted],
            "final_decision": "stale_quote_reject",
        }
        retry_policy_decision = {"decision": "stale_quote_reject", "next_attempt": None, "backoff_seconds": 0.0, "reason": "quote_age_exceeded"}
        final_decision = "stale_quote_reject"
        lifecycle_events = [lifecycle_event_to_dict(submitted)]
        reconciliation_payload = {
            "final_status": "stale_quote_reject",
            "attempts_used": 0,
            "elapsed_seconds": 0.0,
            "retry_decisions": [],
            "final_decision": "stale_quote_reject",
        }
    else:
        reconciliation_obj = reconcile_confirmation_attempts(
            submitted,
            policy=policy,
            outcomes=confirmation_outcomes,
            elapsed_seconds_by_attempt=confirmation_elapsed_seconds_by_attempt if confirmation_elapsed_seconds_by_attempt is not None else [elapsed_seconds],
        )
        reconciliation = reconciliation_obj
        retry_policy_decision = (
            dict(reconciliation_obj.retry_decisions[-1])
            if reconciliation_obj.retry_decisions
            else {"decision": "confirmed", "next_attempt": None, "backoff_seconds": 0.0, "reason": "confirmed"}
        )
        final_decision = reconciliation_obj.final_decision
        lifecycle_events = list(reconciliation_obj.lifecycle_events)
        reconciliation_payload = {
            "final_status": reconciliation_obj.final_status,
            "attempts_used": reconciliation_obj.attempts_used,
            "elapsed_seconds": reconciliation_obj.elapsed_seconds,
            "retry_decisions": list(reconciliation_obj.retry_decisions),
            "final_decision": reconciliation_obj.final_decision,
        }

    outcome_class = _classify_submit_confirm_outcome(final_decision)

    payload = {
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
        "lifecycle_events": lifecycle_events,
        "submit_confirm_summary": {
            "outcome_class": outcome_class,
            "simulated_submit_latency_ms": simulated_submit_latency_ms,
            "quote_age_ms_at_submit": quote_age_ms_at_submit,
            "max_quote_age_ms_before_submit": max_quote_age_ms_before_submit,
            "submit_stale_quote_reject": stale_submit_reject,
            "confirmation_attempts_used": reconciliation_payload["attempts_used"],
            "confirmation_elapsed_seconds": reconciliation_payload["elapsed_seconds"],
            "confirmation_retry_count": len(reconciliation_payload["retry_decisions"]),
        },
        "reconciliation": {
            **reconciliation_payload,
        },
    }
    if (
        signature_status_payload is not None
        or tx_payload is not None
        or preview_estimates is not None
        or chain_reconciliation_thresholds is not None
    ):
        payload["chain_reconciliation"] = reconcile_live_chain_confirmation(
            workflow={
                "final_decision": final_decision,
                "submit_confirm_summary": {
                    "outcome_class": outcome_class,
                    "confirmation_attempts_used": reconciliation_payload["attempts_used"],
                    "confirmation_elapsed_seconds": reconciliation_payload["elapsed_seconds"],
                },
            },
            signature_status_payload=signature_status_payload,
            tx_payload=tx_payload,
            preview_estimates=preview_estimates,
            mismatch_thresholds=chain_reconciliation_thresholds,
            owner_filter=chain_reconciliation_owner_filter,
        )
    return payload


def _classify_submit_confirm_outcome(final_decision: str) -> str:
    decision = str(final_decision or "")
    if decision == "confirmed":
        return "submit_confirm_confirmed"
    if decision == "retry":
        return "submit_confirm_retrying"
    if decision == "timeout":
        return "submit_confirm_timeout"
    if decision == "fail":
        return "submit_confirm_failed"
    if decision == "stale_quote_reject":
        return "submit_rejected_stale_quote"
    return "submit_confirm_unknown"
