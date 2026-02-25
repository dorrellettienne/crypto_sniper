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


def test_dry_run_buy_can_simulate_partial_fill():
    adapter = DryRunExecutionAdapter(execution_realism={"enabled": True, "fill_ratio": 0.5})
    result = adapter.buy("TOKEN", "SYM", 0.01, 100)
    assert result.ok is True
    assert result.metadata["execution_outcome_class"] == "partial_fill"
    assert result.metadata["execution_realism"]["fill_ratio"] == 0.5


def test_dry_run_buy_can_reject_stale_quote():
    adapter = DryRunExecutionAdapter(
        execution_realism={"enabled": True, "simulated_latency_ms": 500, "max_quote_age_ms_at_fill": 100}
    )
    result = adapter.buy("TOKEN", "SYM", 0.01, 100)
    assert result.ok is False
    assert result.metadata["execution_outcome_class"] == "stale_quote_reject"
