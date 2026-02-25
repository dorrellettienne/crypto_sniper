import json
import time
from typing import Any, Callable
from urllib.request import Request, urlopen

from src.live.network_reliability import run_with_retries


class DexScreenerFetchError(Exception):
    pass


def _default_http_get_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    req = Request(url=url, method="GET")
    with urlopen(req, timeout=timeout_seconds) as resp:  # nosec - controlled URL/path, read-only fetch
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise DexScreenerFetchError("DexScreener response must be a JSON object")
    return data


class DexScreenerHttpPairsFetcher:
    """
    Optional network fetcher for DexScreener pairs payloads.
    Adds retry/backoff + transport metadata; remains injectable for tests.
    """

    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: float = 5.0,
        max_attempts: int = 1,
        retry_backoff_seconds: float = 0.0,
        max_payload_age_ms: int | None = None,
        fail_on_stale_payload: bool = True,
        transport: Callable[[str, float], dict[str, Any]] | None = None,
        now_ms_fn: Callable[[], int] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.url = str(url or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.max_payload_age_ms = None if max_payload_age_ms is None else int(max_payload_age_ms)
        self.fail_on_stale_payload = bool(fail_on_stale_payload)
        self._transport = transport or _default_http_get_json
        self._now_ms_fn = now_ms_fn or (lambda: int(time.time() * 1000))
        self._sleep_fn = sleep_fn
        if not self.url:
            raise ValueError("DexScreener fetch url is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.max_payload_age_ms is not None and self.max_payload_age_ms < 0:
            raise ValueError("max_payload_age_ms must be >= 0")

    def __call__(self) -> dict[str, Any]:
        res = run_with_retries(
            lambda: self._transport(self.url, self.timeout_seconds),
            max_attempts=self.max_attempts,
            backoff_seconds=self.retry_backoff_seconds,
            sleep_fn=self._sleep_fn,
        )
        if res.value is None:
            raise DexScreenerFetchError(str(res.final_error or "dexscreener_fetch_failed"))

        payload = dict(res.value)
        now_ms = int(self._now_ms_fn())
        source_fetched_at = payload.get("fetched_at_unix_ms")
        if source_fetched_at is None:
            source_fetched_at = now_ms
        stale = False
        payload_age_ms = 0
        if self.max_payload_age_ms is not None:
            try:
                payload_age_ms = max(0, now_ms - int(source_fetched_at))
                stale = payload_age_ms > self.max_payload_age_ms
            except Exception:
                stale = True
                payload_age_ms = -1
            if stale and self.fail_on_stale_payload:
                raise DexScreenerFetchError("dexscreener_payload_stale")

        payload["fetched_at_unix_ms"] = now_ms
        payload["_fetch_meta"] = {
            "transport": "http_dexscreener",
            "attempts": int(res.attempts),
            "retry_events": int(res.retry_events),
            "error_classification": str(res.error_classification or ""),
            "final_error": str(res.final_error) if res.final_error else "",
            "stale_payload": bool(stale),
            "payload_age_ms": int(payload_age_ms),
        }
        return payload
