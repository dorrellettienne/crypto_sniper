import json

from src.runner.paper_sim_runner import (
    add_cost_estimate_to_result,
    format_simulation_summary,
    format_simulation_summary_csv_row,
    format_simulation_summary_json,
)


def _base_result():
    return {
        "steps": 20,
        "seed": 42,
        "actions_taken": 7,
        "generated_at_utc": "2026-02-23T00:00:00+00:00",
        "summary": {
            "total_trades": 3,
            "total_pnl": 50.0,
            "wins": 2,
            "losses": 1,
            "win_rate": 66.67,
        },
    }


def test_add_cost_estimate_to_result_returns_copy_and_preserves_input():
    result = _base_result()
    original = _base_result()

    enriched = add_cost_estimate_to_result(
        result,
        entry_notional_usd=100.0,
        fee_bps_per_leg=10,
        slippage_bps_per_leg=10,
        network_fee_usd_per_leg=0.0,
    )

    assert result == original
    assert enriched is not result
    assert "cost_estimate" in enriched
    assert enriched["cost_estimate"]["estimated_total_cost_usd"] == 0.5
    assert enriched["cost_estimate"]["estimated_net_pnl"] == 49.5


def test_format_simulation_summary_includes_optional_cost_fields_when_present():
    enriched = add_cost_estimate_to_result(
        _base_result(),
        entry_notional_usd=100.0,
        fee_bps_per_leg=10,
        slippage_bps_per_leg=10,
        network_fee_usd_per_leg=0.0,
    )

    formatted = format_simulation_summary(enriched)

    assert formatted["summary"]["total_pnl"] == 50.0
    assert formatted["summary"]["estimated_total_cost_usd"] == 0.5
    assert formatted["summary"]["estimated_net_pnl"] == 49.5
    assert formatted["cost_model"] == {
        "fee_bps_per_leg": 10.0,
        "slippage_bps_per_leg": 10.0,
        "network_fee_usd_per_leg": 0.0,
    }


def test_format_simulation_summary_json_serializes_cost_fields():
    enriched = add_cost_estimate_to_result(
        _base_result(),
        entry_notional_usd=100.0,
        fee_bps_per_leg=30,
        slippage_bps_per_leg=50,
        network_fee_usd_per_leg=0.25,
    )

    payload = json.loads(format_simulation_summary_json(enriched))

    assert payload["summary"]["estimated_total_cost_usd"] > 0
    assert "cost_model" in payload
    assert payload["cost_model"]["network_fee_usd_per_leg"] == 0.25


def test_format_simulation_summary_csv_row_includes_cost_columns_when_present():
    enriched = add_cost_estimate_to_result(
        _base_result(),
        entry_notional_usd=100.0,
        fee_bps_per_leg=10,
        slippage_bps_per_leg=10,
        network_fee_usd_per_leg=0.0,
    )

    row = format_simulation_summary_csv_row(enriched)

    assert "estimated_total_cost_usd" in row
    assert "estimated_net_pnl" in row
    assert row["estimated_total_cost_usd"] == 0.5
    assert row["estimated_net_pnl"] == 49.5
