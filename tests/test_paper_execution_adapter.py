from src.live.paper_execution_adapter import PaperExecutionAdapter


def test_paper_execution_adapter_buy_wraps_success(monkeypatch):
    monkeypatch.setattr("src.live.paper_execution_adapter.simulate_buy", lambda **kwargs: 123)

    adapter = PaperExecutionAdapter()
    result = adapter.buy("TOKEN", "SYM", 0.01, 50)

    assert result.ok is True
    assert result.action == "buy"
    assert result.position_id == 123


def test_paper_execution_adapter_buy_wraps_block(monkeypatch):
    monkeypatch.setattr("src.live.paper_execution_adapter.simulate_buy", lambda **kwargs: None)

    adapter = PaperExecutionAdapter()
    result = adapter.buy("TOKEN", "SYM", 0.01, 50)

    assert result.ok is False
    assert result.position_id is None


def test_paper_execution_adapter_sell_wraps_pnl(monkeypatch):
    monkeypatch.setattr("src.live.paper_execution_adapter.simulate_sell", lambda position_id, exit_price: 25.0)

    adapter = PaperExecutionAdapter()
    result = adapter.sell(1, 0.02)

    assert result.ok is True
    assert result.action == "sell"
    assert result.pnl == 25.0


def test_paper_execution_adapter_stop_loss_wraps_pnl(monkeypatch):
    monkeypatch.setattr("src.live.paper_execution_adapter.simulate_stop_loss", lambda position_id, stop_percent: -10.0)

    adapter = PaperExecutionAdapter()
    result = adapter.stop_loss(1, 0.1)

    assert result.ok is True
    assert result.action == "stop_loss"
    assert result.pnl == -10.0
