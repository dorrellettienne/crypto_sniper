import pytest

from src.live.live_dex_quote_executor import LiveDexQuoteError, QuoteOnlyDexExecutor


def test_quote_only_dex_executor_build_buy_order_uses_quote_transport():
    seen = {}

    def transport(url, params, timeout_seconds):
        seen["url"] = url
        seen["params"] = dict(params)
        seen["timeout_seconds"] = timeout_seconds
        return {"outAmount": "12345", "routePlan": [{"x": 1}]}

    ex = QuoteOnlyDexExecutor("https://quote.example", timeout_seconds=3, transport=transport)
    order = ex.build_buy_order("TOKEN_A", "TKA", 0.01, 100)

    assert order["action"] == "buy"
    assert order["mode"] == "quote_only"
    assert order["quote_preview"]["out_amount"] == "12345"
    assert order["quote_preview"]["route_count"] == 1
    assert seen["params"]["outputMint"] == "TOKEN_A"
    assert seen["params"]["inputMint"] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def test_quote_only_dex_executor_sell_and_stop_are_preview_only():
    ex = QuoteOnlyDexExecutor("https://quote.example", transport=lambda *args, **kwargs: {})
    sell = ex.build_sell_order(1, 0.02)
    stop = ex.build_stop_loss_order(1, 0.1)
    submit = ex.build_submit_preview({"action": "buy"}, "coid_1")
    assert sell["quote_preview"]["note"]
    assert stop["quote_preview"]["note"]
    assert submit["mode"] == "submit_skeleton"
    assert submit["client_order_id"] == "coid_1"


def test_quote_only_dex_executor_allows_buy_quote_in_non_quote_only_mode_but_rejects_sell_stop():
    ex = QuoteOnlyDexExecutor("https://quote.example", transport=lambda *args, **kwargs: {}, quote_only_mode=False)
    buy = ex.build_buy_order("TOKEN_A", "TKA", 0.01, 10)
    assert buy["action"] == "buy"
    with pytest.raises(LiveDexQuoteError):
        ex.build_sell_order(1, 0.02)
    with pytest.raises(LiveDexQuoteError):
        ex.build_stop_loss_order(1, 0.1)


def test_quote_only_dex_executor_adds_fetch_timestamp_to_preview():
    ex = QuoteOnlyDexExecutor(
        "https://quote.example",
        transport=lambda *args, **kwargs: {"outAmount": "1"},
        now_ms_fn=lambda: 1234567890,
    )
    preview = ex.get_quote_preview(input_mint="USDC", output_mint="TOKEN_A", amount=1, slippage_bps=50)
    assert preview["fetched_at_unix_ms"] == 1234567890


def test_quote_only_dex_executor_build_buy_order_uses_configured_quote_input_mint():
    seen = {}
    def transport(url, params, timeout_seconds):
        seen["params"] = dict(params)
        return {"outAmount": "1"}
    ex = QuoteOnlyDexExecutor("https://quote.example", transport=transport, quote_input_mint="CUSTOM_INPUT_MINT")
    ex.build_buy_order("TOKEN_A", "TKA", 0.01, 1)
    assert seen["params"]["inputMint"] == "CUSTOM_INPUT_MINT"


def test_quote_only_dex_executor_builds_unsigned_submit_stub_for_buy_when_swap_configured():
    seen = {}

    def quote_transport(url, params, timeout_seconds):
        return {"outAmount": "777", "routePlan": [{"leg": 1}], "foo": "bar"}

    def swap_transport(url, body, timeout_seconds):
        seen["url"] = url
        seen["body"] = dict(body)
        seen["timeout_seconds"] = timeout_seconds
        return {
            "swapTransaction": "UNSIGNED_TX_B64",
            "lastValidBlockHeight": 123,
            "prioritizationFeeLamports": 9999,
        }

    ex = QuoteOnlyDexExecutor(
        "https://quote.example",
        transport=quote_transport,
        swap_url="https://swap.example",
        swap_transport=swap_transport,
        swap_user_public_key="WALLET_PUB",
    )
    order = ex.build_buy_order("TOKEN_A", "TKA", 0.01, 10)
    stub = ex.build_unsigned_submit_stub(order, "coid_1")

    assert stub["ready"] is True
    assert stub["unsigned_transaction_base64"] == "UNSIGNED_TX_B64"
    assert stub["reason"] == ""
    assert stub["swap_response_meta"]["last_valid_block_height"] == 123
    assert seen["url"] == "https://swap.example"
    assert seen["body"]["userPublicKey"] == "WALLET_PUB"
    assert isinstance(seen["body"]["quoteResponse"], dict)


def test_quote_only_dex_executor_unsigned_submit_stub_is_fail_safe_without_swap_config():
    ex = QuoteOnlyDexExecutor("https://quote.example", transport=lambda *args, **kwargs: {"outAmount": "1"})
    order = ex.build_buy_order("TOKEN_A", "TKA", 0.01, 10)
    stub = ex.build_unsigned_submit_stub(order, "coid_1")
    assert stub["ready"] is False
    assert stub["unsigned_transaction_base64"] is None
    assert stub["reason"] == "quote_only_executor_no_swap_url"
