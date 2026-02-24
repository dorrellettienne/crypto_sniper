import pytest

from src.live.confirmation_retry_policy import (
    BACKOFF_EXPONENTIAL,
    BACKOFF_LINEAR,
    BACKOFF_NONE,
    ConfirmationRetryPolicy,
    RETRY_DECISION_FAIL,
    RETRY_DECISION_RETRY,
    RETRY_DECISION_TIMEOUT,
    compute_backoff_seconds,
    decide_retry_action,
)


def test_policy_defaults_are_valid():
    policy = ConfirmationRetryPolicy()
    assert policy.max_attempts == 3
    assert policy.backoff_mode == BACKOFF_NONE


def test_policy_validation_rejects_invalid_values():
    with pytest.raises(ValueError):
        ConfirmationRetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        ConfirmationRetryPolicy(timeout_seconds=0)
    with pytest.raises(ValueError):
        ConfirmationRetryPolicy(backoff_mode="weird")
    with pytest.raises(ValueError):
        ConfirmationRetryPolicy(backoff_mode=BACKOFF_LINEAR, backoff_base_seconds=0)


def test_compute_backoff_seconds_modes():
    none_policy = ConfirmationRetryPolicy(backoff_mode=BACKOFF_NONE)
    lin_policy = ConfirmationRetryPolicy(backoff_mode=BACKOFF_LINEAR, backoff_base_seconds=0.5)
    exp_policy = ConfirmationRetryPolicy(backoff_mode=BACKOFF_EXPONENTIAL, backoff_base_seconds=0.5)

    assert compute_backoff_seconds(none_policy, 1) == 0.0
    assert compute_backoff_seconds(lin_policy, 2) == 1.0
    assert compute_backoff_seconds(exp_policy, 3) == 2.0


def test_decide_retry_action_returns_retry_when_allowed():
    policy = ConfirmationRetryPolicy(max_attempts=3, timeout_seconds=10, backoff_mode=BACKOFF_LINEAR, backoff_base_seconds=1)
    decision = decide_retry_action(policy, attempt=1, elapsed_seconds=1.5)

    assert decision["decision"] == RETRY_DECISION_RETRY
    assert decision["next_attempt"] == 2
    assert decision["backoff_seconds"] == 2.0


def test_decide_retry_action_returns_fail_on_max_attempts():
    policy = ConfirmationRetryPolicy(max_attempts=2, timeout_seconds=10)
    decision = decide_retry_action(policy, attempt=2, elapsed_seconds=1)
    assert decision["decision"] == RETRY_DECISION_FAIL
    assert decision["reason"] == "max_attempts_reached"


def test_decide_retry_action_returns_timeout_when_elapsed_exceeds_timeout():
    policy = ConfirmationRetryPolicy(max_attempts=5, timeout_seconds=3)
    decision = decide_retry_action(policy, attempt=1, elapsed_seconds=3.0)
    assert decision["decision"] == RETRY_DECISION_TIMEOUT
    assert decision["reason"] == "timeout_reached"
