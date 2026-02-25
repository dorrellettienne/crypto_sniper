import pytest

from src.live.execution_realism import build_execution_realism_config, simulate_buy_execution_realism


def test_simulate_buy_execution_realism_full_fill_defaults():
    cfg = build_execution_realism_config({"enabled": True})
    out = simulate_buy_execution_realism(
        token_address="T",
        symbol="S",
        entry_price=0.01,
        usd_size=100.0,
        config=cfg,
    )
    assert out["allowed"] is True
    assert out["outcome_class"] == "full_fill"
    assert out["fill_ratio"] == 1.0


def test_simulate_buy_execution_realism_partial_fill():
    cfg = build_execution_realism_config({"enabled": True, "fill_ratio": 0.4})
    out = simulate_buy_execution_realism(token_address="T", symbol="S", entry_price=0.02, usd_size=100.0, config=cfg)
    assert out["allowed"] is True
    assert out["outcome_class"] == "partial_fill"
    assert out["fill_ratio"] == 0.4


def test_simulate_buy_execution_realism_stale_quote_reject():
    cfg = build_execution_realism_config({"enabled": True, "simulated_latency_ms": 500, "max_quote_age_ms_at_fill": 100})
    out = simulate_buy_execution_realism(token_address="T", symbol="S", entry_price=0.02, usd_size=100.0, config=cfg)
    assert out["allowed"] is False
    assert out["outcome_class"] == "stale_quote_reject"


def test_simulate_buy_execution_realism_slippage_tolerance_reject():
    cfg = build_execution_realism_config(
        {
            "enabled": True,
            "expected_slippage_bps": 10,
            "volatility_penalty_bps": 20,
            "max_realized_slippage_bps": 25,
        }
    )
    out = simulate_buy_execution_realism(token_address="T", symbol="S", entry_price=0.02, usd_size=100.0, config=cfg)
    assert out["allowed"] is False
    assert out["outcome_class"] == "slippage_tolerance_exceeded"


def test_build_execution_realism_config_rejects_bad_fill_ratio():
    with pytest.raises(ValueError):
        build_execution_realism_config({"enabled": True, "fill_ratio": 1.5})
