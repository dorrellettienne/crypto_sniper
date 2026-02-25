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
