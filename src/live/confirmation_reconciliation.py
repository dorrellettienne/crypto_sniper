from dataclasses import dataclass
from typing import Any

from src.live.confirmation_retry_policy import (
    ConfirmationRetryPolicy,
    RETRY_DECISION_FAIL,
    RETRY_DECISION_RETRY,
    RETRY_DECISION_TIMEOUT,
    decide_retry_action,
)
from src.live.order_lifecycle import (
    OrderLifecycleEvent,
    lifecycle_event_to_dict,
    mark_confirmed,
    mark_failed,
    mark_retrying,
    mark_timeout,
)


VALID_CONFIRMATION_OUTCOMES = {"confirmed", "pending", "error"}


@dataclass
class ConfirmationReconciliationResult:
    final_status: str
    attempts_used: int
    elapsed_seconds: float
    retry_decisions: list[dict[str, Any]]
    lifecycle_events: list[dict[str, Any]]
    final_decision: str


def _to_int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_signature_status_value(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    # Already a status object shape from getSignatureStatuses.result.value[0]
    if any(k in payload for k in ("confirmationStatus", "confirmations", "err", "slot")):
        return payload

    result = payload.get("result")
    if isinstance(result, dict):
        value = result.get("value")
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) or item is None:
                    return item
        if isinstance(value, dict):
            return value

    value = payload.get("value")
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) or item is None:
                return item
    if isinstance(value, dict):
        return value
    return None


def normalize_signature_confirmation_status(
    signature_status_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Normalize getSignatureStatuses-style payloads into deterministic states.
    """
    status = _extract_signature_status_value(signature_status_payload)
    if status is None:
        return {
            "normalized_status": "rpc_inconclusive",
            "terminal": False,
            "retryable": True,
            "slot": None,
            "error_summary": "missing_signature_status",
            "raw_confirmation_status": None,
        }

    if not isinstance(status, dict):
        return {
            "normalized_status": "rpc_inconclusive",
            "terminal": False,
            "retryable": True,
            "slot": None,
            "error_summary": "invalid_signature_status_shape",
            "raw_confirmation_status": None,
        }

    raw_confirmation_status = status.get("confirmationStatus")
    err = status.get("err")
    slot = _to_int_or_none(status.get("slot"))

    if raw_confirmation_status == "finalized":
        return {
            "normalized_status": "finalized",
            "terminal": True,
            "retryable": False,
            "slot": slot,
            "error_summary": "",
            "raw_confirmation_status": raw_confirmation_status,
        }

    if raw_confirmation_status == "confirmed":
        return {
            "normalized_status": "confirmed",
            "terminal": True,
            "retryable": False,
            "slot": slot,
            "error_summary": "",
            "raw_confirmation_status": raw_confirmation_status,
        }

    if err not in (None, {}):
        err_text = str(err).lower()
        normalized_status = "expired_blockhash" if ("blockhash" in err_text and ("expired" in err_text or "not found" in err_text)) else "dropped"
        return {
            "normalized_status": normalized_status,
            "terminal": True,
            "retryable": False,
            "slot": slot,
            "error_summary": str(err),
            "raw_confirmation_status": raw_confirmation_status,
        }

    # processed / pending / missing are retryable until policy times out
    return {
        "normalized_status": "rpc_inconclusive",
        "terminal": False,
        "retryable": True,
        "slot": slot,
        "error_summary": "",
        "raw_confirmation_status": raw_confirmation_status,
    }


def extract_transaction_settlement_summary(
    tx_payload: dict[str, Any] | None,
    *,
    owner_filter: str | None = None,
) -> dict[str, Any]:
    """
    Parse a getTransaction-style payload (or direct tx/meta dict) into fee and balance deltas.
    Returns read-only accounting hints for reconciliation (not authoritative PnL accounting).
    """
    owner_filter_norm = str(owner_filter or "").strip().lower()

    if not isinstance(tx_payload, dict):
        return {
            "tx_present": False,
            "slot": None,
            "signature": None,
            "fee_lamports": None,
            "net_sol_delta_lamports_total": None,
            "token_deltas_by_mint": {},
            "error_summary": "missing_tx_payload",
        }

    result = tx_payload.get("result") if isinstance(tx_payload.get("result"), dict) else tx_payload
    if not isinstance(result, dict):
        return {
            "tx_present": False,
            "slot": None,
            "signature": None,
            "fee_lamports": None,
            "net_sol_delta_lamports_total": None,
            "token_deltas_by_mint": {},
            "error_summary": "missing_tx_result",
        }

    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    transaction = result.get("transaction") if isinstance(result.get("transaction"), dict) else {}
    slot = _to_int_or_none(result.get("slot"))
    signatures = transaction.get("signatures") if isinstance(transaction.get("signatures"), list) else []
    signature = str(signatures[0]) if signatures else None

    pre_balances = result.get("meta", {}).get("preBalances") if isinstance(result.get("meta"), dict) else None
    post_balances = result.get("meta", {}).get("postBalances") if isinstance(result.get("meta"), dict) else None
    net_sol_delta = None
    if isinstance(pre_balances, list) and isinstance(post_balances, list) and len(pre_balances) == len(post_balances):
        total = 0
        valid = True
        for pre, post in zip(pre_balances, post_balances):
            try:
                total += int(post) - int(pre)
            except (TypeError, ValueError):
                valid = False
                break
        if valid:
            net_sol_delta = total

    pre_token = meta.get("preTokenBalances") if isinstance(meta.get("preTokenBalances"), list) else []
    post_token = meta.get("postTokenBalances") if isinstance(meta.get("postTokenBalances"), list) else []
    token_deltas: dict[str, int] = {}

    def _token_entries(rows: list[Any]) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            mint = str(row.get("mint") or "")
            if not mint:
                continue
            owner = str(row.get("owner") or "")
            if owner_filter_norm and owner.lower() != owner_filter_norm:
                continue
            amount = None
            ui = row.get("uiTokenAmount")
            if isinstance(ui, dict):
                amount = ui.get("amount")
            amt_int = _to_int_or_none(amount)
            if amt_int is None:
                continue
            acct_idx = str(row.get("accountIndex") if row.get("accountIndex") is not None else "")
            out[(mint, acct_idx)] = amt_int
        return out

    pre_map = _token_entries(pre_token)
    post_map = _token_entries(post_token)
    all_keys = set(pre_map) | set(post_map)
    for mint, acct_idx in all_keys:
        delta = int(post_map.get((mint, acct_idx), 0)) - int(pre_map.get((mint, acct_idx), 0))
        if delta != 0:
            token_deltas[mint] = token_deltas.get(mint, 0) + delta

    err = meta.get("err")
    return {
        "tx_present": True,
        "slot": slot,
        "signature": signature,
        "fee_lamports": _to_int_or_none(meta.get("fee")),
        "net_sol_delta_lamports_total": net_sol_delta,
        "token_deltas_by_mint": token_deltas,
        "error_summary": "" if err in (None, {}) else str(err),
    }


def reconcile_live_chain_confirmation(
    *,
    workflow: dict[str, Any] | None,
    signature_status_payload: dict[str, Any] | None = None,
    tx_payload: dict[str, Any] | None = None,
    preview_estimates: dict[str, Any] | None = None,
    mismatch_thresholds: dict[str, Any] | None = None,
    owner_filter: str | None = None,
) -> dict[str, Any]:
    """
    Build a structured reconciliation record comparing workflow expectations vs RPC/tx facts.
    Safe/read-only: consumes payloads, performs no network calls.
    """
    workflow = dict(workflow or {})
    preview_estimates = dict(preview_estimates or {})
    mismatch_thresholds = dict(mismatch_thresholds or {})

    normalized = normalize_signature_confirmation_status(signature_status_payload)
    settlement = extract_transaction_settlement_summary(tx_payload, owner_filter=owner_filter)

    workflow_decision = str(workflow.get("final_decision") or "")
    submit_summary = workflow.get("submit_confirm_summary") if isinstance(workflow.get("submit_confirm_summary"), dict) else {}
    workflow_outcome_class = str((submit_summary or {}).get("outcome_class") or "")

    mismatch_flags: list[str] = []
    normalized_status = normalized["normalized_status"]

    if workflow_decision == "confirmed" and normalized_status not in {"confirmed", "finalized"}:
        mismatch_flags.append("workflow_confirmed_but_chain_not_confirmed")
    if normalized_status in {"confirmed", "finalized"} and not settlement.get("tx_present", False):
        mismatch_flags.append("confirmed_but_tx_missing")
    if normalized_status in {"confirmed", "finalized"} and settlement.get("tx_present", False):
        token_deltas = settlement.get("token_deltas_by_mint") or {}
        if not token_deltas:
            mismatch_flags.append("confirmed_without_token_delta")

    expected_network_fee_usd = preview_estimates.get("network_fee_usd")
    actual_fee_lamports = settlement.get("fee_lamports")
    fee_deviation_pct = None
    max_fee_deviation_pct = mismatch_thresholds.get("max_fee_deviation_pct")
    if expected_network_fee_usd not in (None, "") and actual_fee_lamports not in (None, ""):
        try:
            expected_fee_lamports = float(expected_network_fee_usd) * float(mismatch_thresholds.get("usd_to_lamports", 10_000_000))
            if expected_fee_lamports > 0:
                fee_deviation_pct = round(abs(int(actual_fee_lamports) - expected_fee_lamports) / expected_fee_lamports * 100.0, 6)
                if max_fee_deviation_pct not in (None, "") and fee_deviation_pct > float(max_fee_deviation_pct):
                    mismatch_flags.append("fee_deviation_above_threshold")
        except (TypeError, ValueError, ZeroDivisionError):
            fee_deviation_pct = None

    if normalized_status in {"confirmed", "finalized"}:
        outcome_class = "live_confirmed_reconciled" if not mismatch_flags else "live_reconciliation_mismatch"
        terminal_reason = normalized_status
        retryability = "terminal"
    elif normalized_status in {"dropped", "expired_blockhash"}:
        outcome_class = "live_confirmation_failed"
        terminal_reason = normalized_status
        retryability = "terminal"
    else:
        outcome_class = "live_confirmation_inconclusive"
        terminal_reason = normalized_status
        retryability = "retryable" if normalized.get("retryable", False) else "unknown"

    return {
        "outcome_class": outcome_class,
        "terminal_reason": terminal_reason,
        "retryability": retryability,
        "workflow_final_decision": workflow_decision,
        "workflow_outcome_class": workflow_outcome_class,
        "normalized_confirmation": dict(normalized),
        "settlement_summary": dict(settlement),
        "slot": settlement.get("slot") if settlement.get("slot") is not None else normalized.get("slot"),
        "signature": settlement.get("signature"),
        "error_summary": settlement.get("error_summary") or normalized.get("error_summary") or "",
        "mismatch_flags": mismatch_flags,
        "mismatch_flag_count": len(mismatch_flags),
        "preview_vs_actual": {
            "expected_network_fee_usd": expected_network_fee_usd,
            "actual_fee_lamports": actual_fee_lamports,
            "fee_deviation_pct": fee_deviation_pct,
        },
    }


def reconcile_confirmation_attempts(
    submitted_event: OrderLifecycleEvent,
    policy: ConfirmationRetryPolicy,
    outcomes: list[str] | None = None,
    elapsed_seconds_by_attempt: list[float] | None = None,
) -> ConfirmationReconciliationResult:
    """
    Simulates confirmation/reconciliation transitions for a submitted order event.
    Used by live-path skeletons and dry-run tests; no network calls.
    """
    if submitted_event.status != "submitted":
        raise ValueError("submitted_event must have status 'submitted'")

    outcomes = list(outcomes or ["pending"])
    elapsed_seconds_by_attempt = list(elapsed_seconds_by_attempt or [])
    if not outcomes:
        outcomes = ["pending"]

    current = submitted_event
    lifecycle_events = [lifecycle_event_to_dict(current)]
    retry_decisions: list[dict[str, Any]] = []
    total_elapsed = 0.0

    for index, outcome in enumerate(outcomes, start=1):
        outcome = str(outcome)
        if outcome not in VALID_CONFIRMATION_OUTCOMES:
            raise ValueError(f"invalid confirmation outcome: {outcome}")

        step_elapsed = (
            float(elapsed_seconds_by_attempt[index - 1])
            if index - 1 < len(elapsed_seconds_by_attempt)
            else 0.0
        )
        if step_elapsed < 0:
            raise ValueError("elapsed_seconds_by_attempt values must be >= 0")
        total_elapsed += step_elapsed

        if outcome == "confirmed":
            confirmed = mark_confirmed(current, message="confirmation received")
            lifecycle_events.append(lifecycle_event_to_dict(confirmed))
            return ConfirmationReconciliationResult(
                final_status=confirmed.status,
                attempts_used=confirmed.attempt,
                elapsed_seconds=round(total_elapsed, 6),
                retry_decisions=retry_decisions,
                lifecycle_events=lifecycle_events,
                final_decision="confirmed",
            )

        decision = decide_retry_action(policy, attempt=current.attempt, elapsed_seconds=total_elapsed)
        retry_decisions.append(dict(decision))

        if decision["decision"] == RETRY_DECISION_RETRY:
            current = mark_retrying(
                current,
                message="confirmation pending, retrying",
                metadata={"backoff_seconds": decision["backoff_seconds"], "outcome": outcome},
            )
            lifecycle_events.append(lifecycle_event_to_dict(current))
            continue

        if decision["decision"] == RETRY_DECISION_TIMEOUT:
            timeout_evt = mark_timeout(current, message="confirmation timeout")
            lifecycle_events.append(lifecycle_event_to_dict(timeout_evt))
            return ConfirmationReconciliationResult(
                final_status=timeout_evt.status,
                attempts_used=timeout_evt.attempt,
                elapsed_seconds=round(total_elapsed, 6),
                retry_decisions=retry_decisions,
                lifecycle_events=lifecycle_events,
                final_decision=RETRY_DECISION_TIMEOUT,
            )

        failed_evt = mark_failed(current, message="confirmation failed after max attempts")
        lifecycle_events.append(lifecycle_event_to_dict(failed_evt))
        return ConfirmationReconciliationResult(
            final_status=failed_evt.status,
            attempts_used=failed_evt.attempt,
            elapsed_seconds=round(total_elapsed, 6),
            retry_decisions=retry_decisions,
            lifecycle_events=lifecycle_events,
            final_decision=RETRY_DECISION_FAIL,
        )

    # If outcomes were exhausted after we already transitioned to retrying,
    # return the current state without inventing another policy decision.
    if current.status == "retrying":
        return ConfirmationReconciliationResult(
            final_status=current.status,
            attempts_used=current.attempt,
            elapsed_seconds=round(total_elapsed, 6),
            retry_decisions=retry_decisions,
            lifecycle_events=lifecycle_events,
            final_decision=RETRY_DECISION_RETRY,
        )

    # If outcomes were exhausted without confirmation and without an explicit stop,
    # evaluate one final policy decision at current elapsed time.
    final_decision = decide_retry_action(policy, attempt=current.attempt, elapsed_seconds=total_elapsed)
    retry_decisions.append(dict(final_decision))
    if final_decision["decision"] == RETRY_DECISION_TIMEOUT:
        timeout_evt = mark_timeout(current, message="confirmation timeout")
        lifecycle_events.append(lifecycle_event_to_dict(timeout_evt))
        return ConfirmationReconciliationResult(
            final_status=timeout_evt.status,
            attempts_used=timeout_evt.attempt,
            elapsed_seconds=round(total_elapsed, 6),
            retry_decisions=retry_decisions,
            lifecycle_events=lifecycle_events,
            final_decision=RETRY_DECISION_TIMEOUT,
        )
    if final_decision["decision"] == RETRY_DECISION_FAIL:
        failed_evt = mark_failed(current, message="confirmation failed after max attempts")
        lifecycle_events.append(lifecycle_event_to_dict(failed_evt))
        return ConfirmationReconciliationResult(
            final_status=failed_evt.status,
            attempts_used=failed_evt.attempt,
            elapsed_seconds=round(total_elapsed, 6),
            retry_decisions=retry_decisions,
            lifecycle_events=lifecycle_events,
            final_decision=RETRY_DECISION_FAIL,
        )

    current = mark_retrying(
        current,
        message="confirmation pending, retrying",
        metadata={"backoff_seconds": final_decision["backoff_seconds"], "outcome": "pending"},
    )
    lifecycle_events.append(lifecycle_event_to_dict(current))
    return ConfirmationReconciliationResult(
        final_status=current.status,
        attempts_used=current.attempt,
        elapsed_seconds=round(total_elapsed, 6),
        retry_decisions=retry_decisions,
        lifecycle_events=lifecycle_events,
        final_decision=RETRY_DECISION_RETRY,
    )
