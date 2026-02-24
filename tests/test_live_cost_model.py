import pytest

from src.live.cost_model import (
    apply_costs_to_pnl,
    estimate_net_pnl_from_gross,
    estimate_round_trip_costs,
    estimate_trade_costs,
)


def test_estimate_trade_costs_basic():
    est = estimate_trade_costs(notional_usd=100.0, fee_bps=30, slippage_bps=50, network_fee_usd=0.25)
    # 0.30 + 0.50 + 0.25 = 1.05
    assert est.estimated_fee_usd == 0.3
    assert est.estimated_slippage_usd == 0.5
    assert est.total_cost_usd == 1.05


def test_estimate_round_trip_costs_uses_both_legs():
    out = estimate_round_trip_costs(
        entry_notional_usd=100.0,
        exit_notional_usd=120.0,
        fee_bps_per_leg=10,
        slippage_bps_per_leg=20,
        network_fee_usd_per_leg=0.1,
    )
    assert round(out["entry"].total_cost_usd, 6) == 0.4
    assert round(out["exit"].total_cost_usd, 6) == 0.46
    assert out["total_cost_usd"] == 0.86


def test_apply_costs_to_pnl_and_net_estimator():
    net = apply_costs_to_pnl(50.0, 1.25)
    assert net == 48.75

    summary = estimate_net_pnl_from_gross(
        gross_pnl=50.0,
        entry_notional_usd=100.0,
        exit_notional_usd=100.0,
        fee_bps_per_leg=10,
        slippage_bps_per_leg=10,
        network_fee_usd_per_leg=0.0,
    )
    assert summary["gross_pnl"] == 50.0
    assert summary["estimated_total_cost_usd"] == 0.4
    assert summary["estimated_net_pnl"] == 49.6


def test_cost_model_rejects_negative_inputs():
    with pytest.raises(ValueError):
        estimate_trade_costs(notional_usd=-1)
    with pytest.raises(ValueError):
        estimate_trade_costs(notional_usd=1, fee_bps=-1)
    with pytest.raises(ValueError):
        apply_costs_to_pnl(10, -1)
