from src.live.cost_model import estimate_costs_from_quote_preview


def test_estimate_costs_from_quote_preview_uses_quote_amount_and_route_penalty():
    quote_preview = {
        "provider": "quote_only_dex",
        "amount": 100_000_000,  # 100 USDC in micro-units
        "route_count": 3,
        "raw_quote": {"outAmount": "12345"},
    }

    out = estimate_costs_from_quote_preview(
        quote_preview,
        fee_bps=30,
        slippage_bps=50,
        network_fee_usd=0.1,
    )

    # effective slippage = 50 + (3-1)*5 = 60 bps
    # fee = 0.30, slip = 0.60, net fee = 0.1 => total = 1.0
    assert out["ok"] is True
    assert out["notional_usd"] == 100.0
    assert out["slippage_bps_effective"] == 60.0
    assert out["estimated_total_cost_usd"] == 1.0
    assert out["out_amount"] == "12345"


def test_estimate_costs_from_quote_preview_falls_back_to_notional():
    out = estimate_costs_from_quote_preview(
        {"provider": "quote_only_dex"},
        fee_bps=10,
        slippage_bps=10,
        network_fee_usd=0.0,
        fallback_notional_usd=50.0,
    )
    assert out["ok"] is True
    assert out["notional_usd"] == 50.0
    assert out["estimated_total_cost_usd"] == 0.1


def test_estimate_costs_from_quote_preview_handles_missing_notional():
    out = estimate_costs_from_quote_preview({"route_count": 1})
    assert out["ok"] is False
    assert out["reason"] == "missing_notional"
    assert out["estimated_total_cost_usd"] == 0.0

