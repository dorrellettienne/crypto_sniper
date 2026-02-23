from src.live.dry_run_execution_adapter import DryRunExecutionAdapter


def test_dry_run_buy_returns_success():
    adapter = DryRunExecutionAdapter()
    result = adapter.buy("TOKEN", "SYM", 0.01, 100)

    assert result.ok is True
    assert result.action == "buy"
    assert result.position_id == 1
    assert result.metadata["usd_size"] == 100


def test_dry_run_can_simulate_failure():
    adapter = DryRunExecutionAdapter(fail_actions={"sell"})
    result = adapter.sell(10, 0.02)

    assert result.ok is False
    assert result.action == "sell"
    assert "failure" in result.message
