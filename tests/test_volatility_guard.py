from src.live.volatility_guard import VolatilityGuard


def test_volatility_guard_blocks_on_loss_streak():
    guard = VolatilityGuard(
        enabled=True,
        current_loss_streak_fn=lambda: 3,
        today_realized_pnl_fn=lambda: -20.0,
        max_loss_streak_block=3,
    )
    d = guard.assess(token_address="A", symbol="A", requested_usd_size=100)
    assert d.allowed is False
    assert d.reason == "loss_streak_circuit_breaker"


def test_volatility_guard_blocks_on_session_drawdown():
    pnls = iter([50.0, 20.0])
    guard = VolatilityGuard(
        enabled=True,
        current_loss_streak_fn=lambda: 0,
        today_realized_pnl_fn=lambda: next(pnls),
        max_session_drawdown_usd_block=25.0,
    )
    first = guard.assess(token_address="A", symbol="A", requested_usd_size=100)
    second = guard.assess(token_address="A", symbol="A", requested_usd_size=100)
    assert first.allowed is True
    assert second.allowed is False
    assert second.reason == "drawdown_circuit_breaker"


def test_volatility_guard_derisks_size_before_block():
    guard = VolatilityGuard(
        enabled=True,
        current_loss_streak_fn=lambda: 2,
        today_realized_pnl_fn=lambda: 0.0,
        loss_streak_derisk_threshold=2,
        derisk_size_multiplier=0.5,
        derisk_min_usd_size=20.0,
    )
    d = guard.assess(token_address="A", symbol="A", requested_usd_size=100)
    assert d.allowed is True
    assert d.derisk_applied is True
    assert d.adjusted_usd_size == 50.0


def test_volatility_guard_disabled_allows():
    guard = VolatilityGuard(enabled=False)
    d = guard.assess(token_address="A", symbol="A", requested_usd_size=100)
    assert d.allowed is True
