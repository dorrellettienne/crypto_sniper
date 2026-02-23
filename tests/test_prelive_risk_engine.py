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
