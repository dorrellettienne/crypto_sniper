import pytest

from src.live.confirmation_reconciliation import (
    reconcile_confirmation_attempts,
)
from src.live.confirmation_retry_policy import ConfirmationRetryPolicy
from src.live.order_lifecycle import make_submitted


def _submitted():
    return make_submitted(action="buy", order_id="coid_test", metadata={"x": 1})


def test_reconcile_confirmed_path():
    result = reconcile_confirmation_attempts(
        _submitted(),
        policy=ConfirmationRetryPolicy(max_attempts=3, timeout_seconds=10),
        outcomes=["confirmed"],
        elapsed_seconds_by_attempt=[0.1],
    )

    assert result.final_status == "confirmed"
    assert result.final_decision == "confirmed"
    assert result.attempts_used == 1
    assert len(result.lifecycle_events) == 2
    assert result.lifecycle_events[-1]["status"] == "confirmed"


def test_reconcile_retry_then_confirmed_path():
    result = reconcile_confirmation_attempts(
        _submitted(),
        policy=ConfirmationRetryPolicy(max_attempts=3, timeout_seconds=10, backoff_mode="linear", backoff_base_seconds=0.5),
        outcomes=["pending", "confirmed"],
        elapsed_seconds_by_attempt=[0.1, 0.2],
    )

    assert result.final_status == "confirmed"
    assert result.final_decision == "confirmed"
    assert result.attempts_used == 2
    assert [e["status"] for e in result.lifecycle_events] == ["submitted", "retrying", "confirmed"]
    assert result.retry_decisions[0]["decision"] == "retry"


def test_reconcile_timeout_path():
    result = reconcile_confirmation_attempts(
        _submitted(),
        policy=ConfirmationRetryPolicy(max_attempts=3, timeout_seconds=1),
        outcomes=["pending"],
        elapsed_seconds_by_attempt=[1.0],
    )

    assert result.final_status == "timeout"
    assert result.final_decision == "timeout"
    assert result.lifecycle_events[-1]["status"] == "timeout"


def test_reconcile_fail_after_max_attempts():
    result = reconcile_confirmation_attempts(
        _submitted(),
        policy=ConfirmationRetryPolicy(max_attempts=1, timeout_seconds=10),
        outcomes=["error"],
        elapsed_seconds_by_attempt=[0.1],
    )

    assert result.final_status == "failed"
    assert result.final_decision == "fail"
    assert result.lifecycle_events[-1]["status"] == "failed"


def test_reconcile_rejects_invalid_outcome():
    with pytest.raises(ValueError):
        reconcile_confirmation_attempts(
            _submitted(),
            policy=ConfirmationRetryPolicy(),
            outcomes=["wat"],
        )

