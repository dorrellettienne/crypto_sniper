import subprocess
import sys


print("=== PAPER MODE REGRESSION SUITE ===", flush=True)

test_files = [
    "tests/test_account_equity.py",
    "tests/test_paper_sim_runner_basic.py",
    "tests/test_paper_sim_runner_risk_gaps.py",
    "tests/test_paper_sim_runner_summary_serializer.py",
    "tests/test_paper_sim_runner_summary_json_serializer.py",
    "tests/test_paper_sim_runner_usd_size.py",
    "tests/test_paper_sim_runner_exit_params.py",
    "tests/test_paper_sim_runner_branch_probabilities.py",
    "tests/test_paper_sim_runner_summary_csv_serializer.py",
    "tests/test_paper_sim_runner_csv_export_path_builder.py",
    "tests/test_paper_sim_runner_summary_csv_file_export.py",
    "tests/test_closed_trades_export_rows.py",
    "tests/test_paper_sim_runner_closed_trades_csv_export.py",
    "tests/test_paper_sim_runner_export_path_builder.py",
    "tests/test_paper_sim_runner_summary_file_export.py",
    "tests/test_paper_sim_runner_export_workflow.py",
    "tests/test_paper_sim_runner_cli_export_smoke.py",
    "tests/test_paper_sim_candidate_runner.py",
    "tests/test_paper_sim_experiments.py",
    "tests/test_paper_sim_experiment_preset_batch.py",
    "tests/test_paper_sim_preset_batch_runner.py",
    "tests/test_paper_execution_adapter.py",
    "tests/test_dry_run_execution_adapter.py",
    "tests/test_live_audit_logger.py",
    "tests/test_live_config_validation.py",
    "tests/test_live_retry_simulator.py",
    "tests/test_prelive_orchestrator.py",
    "tests/test_dry_run_flow_demo.py",
    "tests/test_paper_engine_execution.py",
    "tests/test_time_exit.py",
    "tests/test_max_daily_loss.py",
    "tests/test_max_concurrent_positions.py",
    "tests/test_weekly_summary.py",
    "tests/test_daily_summary.py",
    "tests/test_all_time_summary.py",
    "tests/test_trade_streaks.py",
    "tests/test_trade_streaks_edge_cases.py",
    "tests/test_profit_factor.py",
    "tests/test_expectancy.py",
    "tests/test_payoff_ratio.py",
    "tests/test_average_win_loss.py",
    "tests/test_gross_profit_loss.py",
    "tests/test_average_trade_pnl.py",
    "tests/test_best_worst_trade.py",
    "tests/test_median_trade_pnl.py",
    "tests/test_trade_pnl_std_dev.py",
    "tests/test_trade_pnl_variance.py",
    "tests/test_trade_pnl_coefficient_of_variation.py",
    "tests/test_downside_deviation.py",
    "tests/test_upside_deviation.py",
    "tests/test_reporting_integration_flow.py",
    "tests/test_equity_snapshot.py",
    "tests/test_settings.py",
]

result = subprocess.run(
    ["python", "-m", "pytest", "-q", *test_files]
)

print("=== REGRESSION COMPLETE ===", flush=True)
print(f"Exit Code: {result.returncode}")

sys.exit(result.returncode)
