from src.live.network_reliability import (
    RetryingQuoteDexExecutor,
    RetryingRpcMintClient,
    run_with_retries,
)


def test_run_with_retries_retries_then_succeeds():
    calls = {"n": 0}
    sleeps = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("timeout")
        return {"ok": True}

    res = run_with_retries(flaky, max_attempts=3, backoff_seconds=0.1, sleep_fn=lambda s: sleeps.append(s))
    assert res.value == {"ok": True}
    assert res.attempts == 3
    assert res.retry_events == 2
    assert sleeps == [0.1, 0.1]


def test_retrying_quote_dex_executor_adds_reliability_metadata():
    calls = {"n": 0}

    class FakeDex:
        def get_quote_preview(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("timeout")
            return {"provider": "quote_only_dex", "raw_quote": {"outAmount": "1"}}

    ex = RetryingQuoteDexExecutor(FakeDex(), max_attempts=2, backoff_seconds=0.0)
    preview = ex.get_quote_preview(input_mint="USDC", output_mint="A", amount=1, slippage_bps=50)
    assert preview["_reliability"]["attempts"] == 2
    assert preview["_reliability"]["retry_events"] == 1


def test_retrying_rpc_mint_client_adds_reliability_metadata():
    calls = {"n": 0}

    class FakeRpc:
        def get_parsed_mint_authorities(self, mint_address: str):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("connection reset")
            return {"mint_authority": None, "freeze_authority": None}

    rpc = RetryingRpcMintClient(FakeRpc(), max_attempts=2)
    out = rpc.get_parsed_mint_authorities("MINT")
    assert out["_reliability"]["attempts"] == 2
    assert out["_reliability"]["retry_events"] == 1
