from dataclasses import dataclass


BACKOFF_NONE = "none"
BACKOFF_LINEAR = "linear"
BACKOFF_EXPONENTIAL = "exponential"

VALID_BACKOFF_MODES = {BACKOFF_NONE, BACKOFF_LINEAR, BACKOFF_EXPONENTIAL}

RETRY_DECISION_RETRY = "retry"
RETRY_DECISION_FAIL = "fail"
RETRY_DECISION_TIMEOUT = "timeout"


@dataclass
class ConfirmationRetryPolicy:
    max_attempts: int = 3
    timeout_seconds: float = 10.0
    backoff_mode: str = BACKOFF_NONE
    backoff_base_seconds: float = 0.0

    def __post_init__(self):
        self.max_attempts = int(self.max_attempts)
        self.timeout_seconds = float(self.timeout_seconds)
        self.backoff_mode = str(self.backoff_mode)
        self.backoff_base_seconds = float(self.backoff_base_seconds)

        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be > 0")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.backoff_mode not in VALID_BACKOFF_MODES:
            raise ValueError(f"invalid backoff_mode: {self.backoff_mode}")
        if self.backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds must be >= 0")
        if self.backoff_mode != BACKOFF_NONE and self.backoff_base_seconds <= 0:
            raise ValueError("backoff_base_seconds must be > 0 when backoff is enabled")


def compute_backoff_seconds(policy: ConfirmationRetryPolicy, attempt: int) -> float:
    attempt = int(attempt)
    if attempt <= 0:
        raise ValueError("attempt must be > 0")
    if policy.backoff_mode == BACKOFF_NONE:
        return 0.0
    if policy.backoff_mode == BACKOFF_LINEAR:
        return round(policy.backoff_base_seconds * attempt, 6)
    # exponential
    return round(policy.backoff_base_seconds * (2 ** (attempt - 1)), 6)


def decide_retry_action(
    policy: ConfirmationRetryPolicy,
    attempt: int,
    elapsed_seconds: float,
) -> dict:
    """
    Returns structured retry/timeout decision for a confirmation/execution attempt.
    """
    attempt = int(attempt)
    elapsed_seconds = float(elapsed_seconds)
    if attempt <= 0:
        raise ValueError("attempt must be > 0")
    if elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must be >= 0")

    if elapsed_seconds >= policy.timeout_seconds:
        return {
            "decision": RETRY_DECISION_TIMEOUT,
            "next_attempt": None,
            "backoff_seconds": 0.0,
            "reason": "timeout_reached",
        }

    if attempt >= policy.max_attempts:
        return {
            "decision": RETRY_DECISION_FAIL,
            "next_attempt": None,
            "backoff_seconds": 0.0,
            "reason": "max_attempts_reached",
        }

    next_attempt = attempt + 1
    return {
        "decision": RETRY_DECISION_RETRY,
        "next_attempt": next_attempt,
        "backoff_seconds": compute_backoff_seconds(policy, next_attempt),
        "reason": "retry_allowed",
    }
