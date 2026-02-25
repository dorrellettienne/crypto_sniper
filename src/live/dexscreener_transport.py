import json
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.live.network_reliability import run_with_retries


class DexScreenerFetchError(Exception):
    def __init__(self, message: str, *, fetch_meta: dict[str, Any] | None = None):
        super().__init__(message)
        self.fetch_meta = dict(fetch_meta or {})


def _default_http_get_json(url: str, timeout_seconds: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req = Request(url=url, method="GET", headers=dict(headers or {}))
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
        fallback_urls: list[str] | None = None,
        timeout_seconds: float = 5.0,
        max_attempts: int = 1,
        retry_backoff_seconds: float = 0.0,
        max_payload_age_ms: int | None = None,
        fail_on_stale_payload: bool = True,
        headers: dict[str, str] | None = None,
        transport: Callable[..., dict[str, Any]] | None = None,
        now_ms_fn: Callable[[], int] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.url = str(url or "").strip()
        self.fallback_urls = [str(u).strip() for u in list(fallback_urls or []) if str(u or "").strip()]
        self.timeout_seconds = float(timeout_seconds)
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.max_payload_age_ms = None if max_payload_age_ms is None else int(max_payload_age_ms)
        self.fail_on_stale_payload = bool(fail_on_stale_payload)
        self.headers = dict(headers or {})
        self._transport = transport or _default_http_get_json
        self._now_ms_fn = now_ms_fn or (lambda: int(time.time() * 1000))
        self._sleep_fn = sleep_fn
        if not self.url:
            raise ValueError("DexScreener fetch url is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.max_payload_age_ms is not None and self.max_payload_age_ms < 0:
            raise ValueError("max_payload_age_ms must be >= 0")

    @staticmethod
    def _classify_exception(exc: Exception | None) -> str:
        if exc is None:
            return ""
        if isinstance(exc, HTTPError):
            return f"http_{int(getattr(exc, 'code', 0))}"
        if isinstance(exc, URLError):
            return "url_error"
        msg = str(exc).lower()
        if "timed out" in msg or "timeout" in msg:
            return "timeout"
        if "getaddrinfo" in msg or "name or service not known" in msg:
            return "dns"
        return "exception"

    def _transport_call(self, url: str) -> dict[str, Any]:
        try:
            return self._transport(url, self.timeout_seconds, self.headers)
        except TypeError:
            # Backward compatibility with injected two-arg transports in tests/callers.
            return self._transport(url, self.timeout_seconds)

    def __call__(self) -> dict[str, Any]:
        endpoint_attempts_meta: list[dict[str, Any]] = []
        selected_url = ""
        last_exc: Exception | None = None
        urls = [self.url, *self.fallback_urls]
        res = None
        for idx, endpoint_url in enumerate(urls):
            endpoint_res = run_with_retries(
                lambda endpoint_url=endpoint_url: self._transport_call(endpoint_url),
                max_attempts=self.max_attempts,
                backoff_seconds=self.retry_backoff_seconds,
                sleep_fn=self._sleep_fn,
            )
            endpoint_attempts_meta.append(
                {
                    "url": endpoint_url,
                    "attempts": int(endpoint_res.attempts),
                    "retry_events": int(endpoint_res.retry_events),
                    "error_classification": str(endpoint_res.error_classification or ""),
                    "final_error": str(endpoint_res.final_error) if endpoint_res.final_error else "",
                    "success": endpoint_res.value is not None,
                    "endpoint_index": idx,
                }
            )
            if endpoint_res.value is not None:
                res = endpoint_res
                selected_url = endpoint_url
                break
            if endpoint_res.final_error is not None:
                last_exc = endpoint_res.final_error

        if res is None or res.value is None:
            classification = self._classify_exception(last_exc)
            raise DexScreenerFetchError(
                str(last_exc or "dexscreener_fetch_failed"),
                fetch_meta={
                    "transport": "http_dexscreener",
                    "attempts": 0,
                    "retry_events": sum(int(item.get("retry_events", 0) or 0) for item in endpoint_attempts_meta),
                    "error_classification": str(classification or ""),
                    "final_error": str(last_exc) if last_exc else "",
                    "selected_url": "",
                    "endpoint_attempts": endpoint_attempts_meta,
                    "headers_applied": sorted(list(self.headers.keys())),
                },
            ) from last_exc

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
                raise DexScreenerFetchError(
                    "dexscreener_payload_stale",
                    fetch_meta={
                        "transport": "http_dexscreener",
                        "attempts": int(res.attempts),
                        "retry_events": int(res.retry_events),
                        "error_classification": str(res.error_classification or ""),
                        "final_error": str(res.final_error) if res.final_error else "",
                        "stale_payload": True,
                        "payload_age_ms": int(payload_age_ms),
                        "selected_url": selected_url,
                        "endpoint_attempts": endpoint_attempts_meta,
                        "headers_applied": sorted(list(self.headers.keys())),
                    },
                )

        payload["fetched_at_unix_ms"] = now_ms
        payload["_fetch_meta"] = {
            "transport": "http_dexscreener",
            "attempts": int(res.attempts),
            "retry_events": int(res.retry_events),
            "error_classification": str(res.error_classification or ""),
            "final_error": str(res.final_error) if res.final_error else "",
            "stale_payload": bool(stale),
            "payload_age_ms": int(payload_age_ms),
            "selected_url": selected_url,
            "endpoint_attempts": endpoint_attempts_meta,
            "headers_applied": sorted(list(self.headers.keys())),
        }
        return payload
