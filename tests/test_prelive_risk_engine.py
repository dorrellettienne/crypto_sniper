from src.live.prelive_risk_engine import PreLiveRiskEngine


def test_prelive_risk_engine_blocks_invalid_usd_size():
    engine = PreLiveRiskEngine()
    decision = engine.can_buy("T", "S", 0)
    assert decision.allowed is False
    assert decision.reason == "invalid_usd_size"


def test_prelive_risk_engine_blocks_max_daily_loss(monkeypatch):
    monkeypatch.setattr("src.live.prelive_risk_engine.get_today_realized_pnl", lambda: -50.0)
    monkeypatch.setattr("src.live.prelive_risk_engine.get_open_position_count", lambda: 0)
    engine = PreLiveRiskEngine()
    decision = engine.can_buy("T", "S", 100)
    assert decision.allowed is False
    assert decision.reason == "max_daily_loss_reached"


def test_prelive_risk_engine_blocks_max_open_positions(monkeypatch):
    monkeypatch.setattr("src.live.prelive_risk_engine.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("src.live.prelive_risk_engine.get_open_position_count", lambda: 3)
    engine = PreLiveRiskEngine()
    decision = engine.can_buy("T", "S", 100)
    assert decision.allowed is False
    assert decision.reason == "max_concurrent_positions_reached"


def test_prelive_risk_engine_allows_when_limits_ok(monkeypatch):
    monkeypatch.setattr("src.live.prelive_risk_engine.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("src.live.prelive_risk_engine.get_open_position_count", lambda: 0)
    engine = PreLiveRiskEngine()
    decision = engine.can_buy("T", "S", 100)
    assert decision.allowed is True
    assert decision.reason == ""


def test_prelive_risk_engine_blocks_usd_bounds(monkeypatch):
    monkeypatch.setattr("src.live.prelive_risk_engine.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("src.live.prelive_risk_engine.get_open_position_count", lambda: 0)
    engine = PreLiveRiskEngine(min_usd_size=50, max_usd_size=200)

    low = engine.can_buy("T", "S", 10)
    high = engine.can_buy("T", "S", 500)

    assert low.allowed is False and low.reason == "usd_size_below_min"
    assert high.allowed is False and high.reason == "usd_size_above_max"


def test_prelive_risk_engine_allowlist_blocklist_and_symbol_policy(monkeypatch):
    monkeypatch.setattr("src.live.prelive_risk_engine.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("src.live.prelive_risk_engine.get_open_position_count", lambda: 0)
    engine = PreLiveRiskEngine(
        token_allowlist=["ALLOW_1", "BLOCK_1"],
        token_blocklist=["BLOCK_1"],
        symbol_allowlist=["GOOD"],
    )

    assert engine.can_buy("OTHER", "GOOD", 100).reason == "token_not_allowlisted"
    assert engine.can_buy("BLOCK_1", "GOOD", 100).reason == "token_blocklisted"
    assert engine.can_buy("ALLOW_1", "BAD", 100).reason == "symbol_not_allowlisted"
    ok = engine.can_buy("ALLOW_1", "GOOD", 100)
    assert ok.allowed is True


def test_prelive_risk_engine_token_cooldown_blocks_repeated_signal(monkeypatch):
    monkeypatch.setattr("src.live.prelive_risk_engine.get_today_realized_pnl", lambda: 0.0)
    monkeypatch.setattr("src.live.prelive_risk_engine.get_open_position_count", lambda: 0)
    engine = PreLiveRiskEngine(token_cooldown_calls=1)

    d1 = engine.can_buy("TOKEN_A", "A", 100)
    d2 = engine.can_buy("TOKEN_A", "A", 100)
    d3 = engine.can_buy("TOKEN_B", "B", 100)
    d4 = engine.can_buy("TOKEN_A", "A", 100)

    assert d1.allowed is True
    assert d2.allowed is False and d2.reason == "token_cooldown_active"
    assert d3.allowed is True
    assert d4.allowed is True
