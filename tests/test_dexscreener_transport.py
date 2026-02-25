import pytest

from src.live.dexscreener_transport import DexScreenerFetchError, DexScreenerHttpPairsFetcher


def test_dexscreener_http_fetcher_retries_and_adds_fetch_meta():
    calls = {"n": 0}
    sleeps = []

    def transport(url, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("timeout")
        return {"pairs": []}

    fetcher = DexScreenerHttpPairsFetcher(
        url="https://example.test/pairs",
        max_attempts=2,
        retry_backoff_seconds=0.1,
        transport=transport,
        now_ms_fn=lambda: 1000,
        sleep_fn=lambda s: sleeps.append(s),
    )
    payload = fetcher()
    assert payload["_fetch_meta"]["retry_events"] == 1
    assert payload["fetched_at_unix_ms"] == 1000
    assert sleeps == [0.1]


def test_dexscreener_http_fetcher_blocks_stale_payload_when_configured():
    fetcher = DexScreenerHttpPairsFetcher(
        url="https://example.test/pairs",
        transport=lambda url, timeout: {"pairs": [], "fetched_at_unix_ms": 1000},
        now_ms_fn=lambda: 5000,
        max_payload_age_ms=100,
        fail_on_stale_payload=True,
    )
    with pytest.raises(DexScreenerFetchError):
        fetcher()


def test_dexscreener_http_fetcher_marks_stale_payload_when_allowed():
    fetcher = DexScreenerHttpPairsFetcher(
        url="https://example.test/pairs",
        transport=lambda url, timeout: {"pairs": [], "fetched_at_unix_ms": 1000},
        now_ms_fn=lambda: 5000,
        max_payload_age_ms=100,
        fail_on_stale_payload=False,
    )
    payload = fetcher()
    assert payload["_fetch_meta"]["stale_payload"] is True


def test_dexscreener_http_fetcher_passes_headers_to_transport_when_supported():
    seen = {}

    def transport(url, timeout, headers=None):
        seen["url"] = url
        seen["timeout"] = timeout
        seen["headers"] = dict(headers or {})
        return {"pairs": []}

    fetcher = DexScreenerHttpPairsFetcher(
        url="https://example.test/pairs",
        headers={"User-Agent": "crypto-sniper-test", "Accept": "application/json"},
        transport=transport,
        now_ms_fn=lambda: 1000,
    )
    payload = fetcher()
    assert seen["headers"]["User-Agent"] == "crypto-sniper-test"
    assert "User-Agent" in payload["_fetch_meta"]["headers_applied"]


def test_dexscreener_http_fetcher_uses_fallback_url_and_records_endpoint_attempts():
    calls = []

    def transport(url, timeout, headers=None):
        calls.append(url)
        if "primary" in url:
            raise RuntimeError("403 forbidden")
        return {"pairs": []}

    fetcher = DexScreenerHttpPairsFetcher(
        url="https://example.test/primary",
        fallback_urls=["https://example.test/fallback"],
        transport=transport,
        now_ms_fn=lambda: 1000,
    )
    payload = fetcher()
    meta = payload["_fetch_meta"]
    assert calls == ["https://example.test/primary", "https://example.test/fallback"]
    assert meta["selected_url"] == "https://example.test/fallback"
    assert len(meta["endpoint_attempts"]) == 2
    assert meta["endpoint_attempts"][0]["success"] is False
    assert meta["endpoint_attempts"][1]["success"] is True


def test_dexscreener_http_fetcher_all_endpoints_fail_raises_structured_error_meta():
    def transport(url, timeout, headers=None):
        raise RuntimeError(f"403 forbidden for {url}")

    fetcher = DexScreenerHttpPairsFetcher(
        url="https://example.test/primary",
        fallback_urls=["https://example.test/fallback1", "https://example.test/fallback2"],
        transport=transport,
        now_ms_fn=lambda: 1000,
    )
    with pytest.raises(DexScreenerFetchError) as excinfo:
        fetcher()
    err = excinfo.value
    assert isinstance(getattr(err, "fetch_meta", None), dict)
    meta = err.fetch_meta
    assert meta["selected_url"] == ""
    assert meta["retry_events"] == 0
    assert len(meta["endpoint_attempts"]) == 3
    assert all(item["success"] is False for item in meta["endpoint_attempts"])
