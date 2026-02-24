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
