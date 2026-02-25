from dataclasses import dataclass
from time import sleep as _sleep
from typing import Any, Callable


@dataclass
class RetryResult:
    value: Any = None
    attempts: int = 0
    retry_events: int = 0
    final_error: Exception | None = None
    error_classification: str = ""


def classify_reliability_error(exc: Exception) -> str:
    text = str(exc or "").lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "429" in text or "rate limit" in text:
        return "rate_limited"
    if "connection" in text or "reset" in text or "temporar" in text:
        return "transient_network"
    return "unknown"


def run_with_retries(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 1,
    backoff_seconds: float = 0.0,
    retry_classifier: Callable[[Exception], bool] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> RetryResult:
    max_attempts = max(1, int(max_attempts))
    backoff_seconds = max(0.0, float(backoff_seconds))
    sleep_fn = sleep_fn or _sleep
    retry_classifier = retry_classifier or (lambda exc: True)

    result = RetryResult()
    for idx in range(max_attempts):
        result.attempts = idx + 1
        try:
            result.value = fn()
            return result
        except Exception as exc:  # pragma: no cover - covered through behavior tests
            result.final_error = exc
            result.error_classification = classify_reliability_error(exc)
            if idx + 1 >= max_attempts or not bool(retry_classifier(exc)):
                return result
            result.retry_events += 1
            if backoff_seconds > 0:
                sleep_fn(backoff_seconds)
    return result


class RetryingQuoteDexExecutor:
    """
    Wraps QuoteOnlyDexExecutor-like objects and retries quote reads.
    Appends reliability metadata into quote previews under `_reliability`.
    """

    def __init__(
        self,
        base_executor: Any,
        *,
        max_attempts: int = 1,
        backoff_seconds: float = 0.0,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.base_executor = base_executor
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.sleep_fn = sleep_fn

    def get_quote_preview(self, **kwargs) -> dict[str, Any]:
        res = run_with_retries(
            lambda: self.base_executor.get_quote_preview(**kwargs),
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            sleep_fn=self.sleep_fn,
        )
        if res.final_error is not None and res.value is None:
            raise res.final_error
        preview = dict(res.value or {})
        preview["_reliability"] = {
            "attempts": int(res.attempts),
            "retry_events": int(res.retry_events),
            "error_classification": str(res.error_classification or ""),
            "final_error": str(res.final_error) if res.final_error else "",
        }
        return preview


class RetryingRpcMintClient:
    """
    Wraps rpc clients exposing `get_parsed_mint_authorities` and adds retries + metadata.
    """

    def __init__(
        self,
        base_client: Any,
        *,
        max_attempts: int = 1,
        backoff_seconds: float = 0.0,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.base_client = base_client
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.sleep_fn = sleep_fn

    def get_parsed_mint_authorities(self, mint_address: str) -> dict[str, Any]:
        res = run_with_retries(
            lambda: self.base_client.get_parsed_mint_authorities(mint_address),
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            sleep_fn=self.sleep_fn,
        )
        if res.final_error is not None and res.value is None:
            raise res.final_error
        out = dict(res.value or {})
        out["_reliability"] = {
            "attempts": int(res.attempts),
            "retry_events": int(res.retry_events),
            "error_classification": str(res.error_classification or ""),
            "final_error": str(res.final_error) if res.final_error else "",
        }
        return out
