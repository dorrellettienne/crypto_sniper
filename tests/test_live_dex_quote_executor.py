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


def test_quote_only_dex_executor_sell_and_stop_are_preview_only():
    ex = QuoteOnlyDexExecutor("https://quote.example", transport=lambda *args, **kwargs: {})
    sell = ex.build_sell_order(1, 0.02)
    stop = ex.build_stop_loss_order(1, 0.1)
    submit = ex.build_submit_preview({"action": "buy"}, "coid_1")
    assert sell["quote_preview"]["note"]
    assert stop["quote_preview"]["note"]
    assert submit["mode"] == "submit_skeleton"
    assert submit["client_order_id"] == "coid_1"


def test_quote_only_dex_executor_rejects_non_quote_only_mode():
    ex = QuoteOnlyDexExecutor("https://quote.example", transport=lambda *args, **kwargs: {}, quote_only_mode=False)
    with pytest.raises(LiveDexQuoteError):
        ex.build_buy_order("TOKEN_A", "TKA", 0.01, 10)
