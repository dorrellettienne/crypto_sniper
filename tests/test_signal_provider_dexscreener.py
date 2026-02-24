from src.live.signal_provider_dexscreener import (
    DexScreenerSignalProvider,
    parse_dexscreener_pairs_to_signals,
)


def _payload():
    return {
        "pairs": [
            {
                "chainId": "solana",
                "pairAddress": "PAIR1",
                "priceUsd": "0.01",
                "pairCreatedAt": 1700000000000,
                "liquidity": {"usd": 15000},
                "baseToken": {"address": "TOKEN1", "symbol": "TK1"},
            },
            {
                "chainId": "ethereum",
                "pairAddress": "PAIR2",
                "priceUsd": "0.02",
                "pairCreatedAt": 1700000000000,
                "liquidity": {"usd": 5000},
                "baseToken": {"address": "TOKEN2", "symbol": "TK2"},
            },
        ]
    }


def test_parse_dexscreener_pairs_to_signals_basic_mapping():
    signals = parse_dexscreener_pairs_to_signals(_payload(), default_usd_size=123, now_ts=1700000060)
    assert len(signals) == 2
    assert signals[0].token_address == "TOKEN1"
    assert signals[0].usd_size == 123.0
    assert signals[0].metadata["source"] == "dexscreener"
    assert signals[0].metadata["token_age_seconds"] == 60.0


def test_parse_dexscreener_pairs_to_signals_filters_chain_liquidity_and_age():
    signals = parse_dexscreener_pairs_to_signals(
        _payload(),
        chain_id="solana",
        min_liquidity_usd=10000,
        max_pair_age_seconds=30,
        now_ts=1700000060,
    )
    # Solana pair is 60s old and should be filtered by max age.
    assert signals == []


def test_parse_dexscreener_pairs_to_signals_skips_invalid_rows():
    payload = {"pairs": [{"chainId": "solana", "baseToken": {"address": "A", "symbol": "A"}, "priceUsd": "bad"}]}
    signals = parse_dexscreener_pairs_to_signals(payload)
    assert signals == []


def test_dexscreener_signal_provider_polls_and_buffers():
    state = {"n": 0}

    def fetcher():
        state["n"] += 1
        if state["n"] == 1:
            return _payload()
        return {"pairs": []}

    provider = DexScreenerSignalProvider(fetcher, default_usd_size=50, chain_id="solana", now_ts_fn=lambda: 1700000060)
    s1 = provider.get_next_signal()
    s2 = provider.get_next_signal()

    assert s1 is not None
    assert s1.token_address == "TOKEN1"
    assert s1.usd_size == 50.0
    assert s2 is None
    assert provider.poll_count >= 2


def test_dexscreener_signal_provider_swallows_parse_errors_when_enabled():
    provider = DexScreenerSignalProvider(lambda: {"pairs": "bad"}, swallow_fetch_errors=True)
    assert provider.get_next_signal() is None
    assert provider.fetch_errors == 1
