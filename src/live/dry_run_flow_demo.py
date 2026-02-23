import argparse

from src.live.audit_logger import build_audit_log_path
from src.live.dry_run_execution_adapter import DryRunExecutionAdapter
from src.live.audit_logger import append_audit_event
from src.live.prelive_orchestrator import AllowAllRiskEngine, execute_buy_with_controls, execute_sell_with_retry
from src.runner.paper_sim_candidate_runner import (
    DEFAULT_CANDIDATE_PRESET_NAME,
    DEFAULT_CANDIDATE_PRESETS_PATH,
    get_candidate_preset,
)


class ToggleFailSellAdapter(DryRunExecutionAdapter):
    """
    Fails the first N sell attempts only, then succeeds (dry-run).
    """

    def __init__(self, fail_sell_attempts: int = 0):
        super().__init__(fail_actions=None)
        self._remaining_sell_failures = max(0, int(fail_sell_attempts))

    def sell(self, position_id: int, exit_price: float):
        if self._remaining_sell_failures > 0:
            self._remaining_sell_failures -= 1
            return type(super().sell(position_id, exit_price))(
                ok=False,
                action="sell",
                position_id=position_id,
                pnl=None,
                message="dry-run simulated temporary sell failure",
                metadata={"exit_price": exit_price},
            )
        return super().sell(position_id, exit_price)


def run_dry_run_demo(
    audit_log_dir: str = "data/exports",
    fail_sell_once: bool = False,
) -> dict:
    audit_log_path = build_audit_log_path(audit_log_dir, prefix="dry_run_flow_demo")
    adapter = DryRunExecutionAdapter(fail_actions={"sell"} if fail_sell_once else None)
    risk_engine = AllowAllRiskEngine()

    buy_result = execute_buy_with_controls(
        adapter=adapter,
        risk_engine=risk_engine,
        audit_log_path=audit_log_path,
        token_address="DRY_TOKEN",
        symbol="DRY",
        entry_price=0.01,
        usd_size=100.0,
    )

    sell_result = execute_sell_with_retry(
        adapter=adapter,
        audit_log_path=audit_log_path,
        position_id=1,
        exit_price=0.02,
        max_attempts=2,
    )

    return {
        "audit_log_path": audit_log_path,
        "buy_result": buy_result,
        "sell_result": sell_result,
    }


def run_dry_run_orchestration_loop(
    iterations: int = 3,
    audit_log_dir: str = "data/exports",
    fail_sell_attempts: int = 0,
) -> dict:
    if iterations <= 0:
        raise ValueError("iterations must be > 0")

    audit_log_path = build_audit_log_path(audit_log_dir, prefix="dry_run_orchestration_loop")
    adapter = ToggleFailSellAdapter(fail_sell_attempts=fail_sell_attempts)
    risk_engine = AllowAllRiskEngine()
    cycle_results = []

    append_audit_event(audit_log_path, "loop_started", {"iterations": iterations, "fail_sell_attempts": fail_sell_attempts})

    for i in range(iterations):
        buy_result = execute_buy_with_controls(
            adapter=adapter,
            risk_engine=risk_engine,
            audit_log_path=audit_log_path,
            token_address=f"DRY_TOKEN_{i}",
            symbol="DRY",
            entry_price=0.01,
            usd_size=100.0,
        )
        sell_result = execute_sell_with_retry(
            adapter=adapter,
            audit_log_path=audit_log_path,
            position_id=1,
            exit_price=0.02,
            max_attempts=3,
        )
        cycle = {
            "cycle_index": i + 1,
            "buy_ok": bool(buy_result.get("ok")),
            "sell_ok": bool(sell_result.get("ok")),
            "sell_attempts": int(sell_result.get("attempts", 0)),
        }
        cycle_results.append(cycle)
        append_audit_event(audit_log_path, "loop_cycle_completed", cycle)

    append_audit_event(
        audit_log_path,
        "loop_completed",
        {
            "iterations": iterations,
            "successful_sells": sum(1 for c in cycle_results if c["sell_ok"]),
            "failed_sells": sum(1 for c in cycle_results if not c["sell_ok"]),
        },
    )

    return {"audit_log_path": audit_log_path, "iterations": iterations, "cycles": cycle_results}


def run_candidate_preset_dry_run_loop(
    preset_name: str = DEFAULT_CANDIDATE_PRESET_NAME,
    presets_json_path: str = DEFAULT_CANDIDATE_PRESETS_PATH,
    iterations: int = 3,
    audit_log_dir: str = "data/exports",
    fail_sell_attempts: int = 0,
) -> dict:
    if iterations <= 0:
        raise ValueError("iterations must be > 0")

    preset = get_candidate_preset(preset_name=preset_name, presets_path=presets_json_path)
    audit_log_path = build_audit_log_path(audit_log_dir, prefix=f"{preset['name']}_dry_run_loop")
    adapter = ToggleFailSellAdapter(fail_sell_attempts=fail_sell_attempts)
    risk_engine = AllowAllRiskEngine()
    cycles = []

    append_audit_event(
        audit_log_path,
        "candidate_loop_started",
        {
            "preset_name": preset["name"],
            "iterations": iterations,
            "fail_sell_attempts": fail_sell_attempts,
            "usd_size": preset["usd_size"],
            "sell_price": preset["sell_price"],
            "stop_loss_percent": preset["stop_loss_percent"],
        },
    )

    for i in range(iterations):
        buy_result = execute_buy_with_controls(
            adapter=adapter,
            risk_engine=risk_engine,
            audit_log_path=audit_log_path,
            token_address=f"CAND_TOKEN_{i}",
            symbol=str(preset["name"])[:12].upper(),
            entry_price=0.01,
            usd_size=float(preset["usd_size"]),
        )
        sell_result = execute_sell_with_retry(
            adapter=adapter,
            audit_log_path=audit_log_path,
            position_id=1,
            exit_price=float(preset["sell_price"]),
            max_attempts=3,
        )
        cycle = {
            "cycle_index": i + 1,
            "preset_name": preset["name"],
            "buy_ok": bool(buy_result.get("ok")),
            "sell_ok": bool(sell_result.get("ok")),
            "sell_attempts": int(sell_result.get("attempts", 0)),
            "sell_price": float(preset["sell_price"]),
            "usd_size": float(preset["usd_size"]),
        }
        cycles.append(cycle)
        append_audit_event(audit_log_path, "candidate_loop_cycle_completed", cycle)

    append_audit_event(
        audit_log_path,
        "candidate_loop_completed",
        {
            "preset_name": preset["name"],
            "iterations": iterations,
            "successful_sells": sum(1 for c in cycles if c["sell_ok"]),
            "failed_sells": sum(1 for c in cycles if not c["sell_ok"]),
        },
    )

    return {"audit_log_path": audit_log_path, "preset": preset, "iterations": iterations, "cycles": cycles}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-log-dir", type=str, default="data/exports")
    parser.add_argument("--fail-sell-once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--fail-sell-attempts", type=int, default=0)
    parser.add_argument("--candidate-preset-loop", action="store_true")
    parser.add_argument("--candidate-preset-name", type=str, default=DEFAULT_CANDIDATE_PRESET_NAME)
    parser.add_argument("--candidate-presets-json-path", type=str, default=DEFAULT_CANDIDATE_PRESETS_PATH)
    args = parser.parse_args()

    if args.candidate_preset_loop:
        result = run_candidate_preset_dry_run_loop(
            preset_name=args.candidate_preset_name,
            presets_json_path=args.candidate_presets_json_path,
            iterations=args.iterations,
            audit_log_dir=args.audit_log_dir,
            fail_sell_attempts=args.fail_sell_attempts,
        )
        print("=== CANDIDATE PRESET DRY RUN LOOP COMPLETE ===")
        print(f"Audit Log: {result['audit_log_path']}")
        print(f"Preset: {result['preset']['name']}")
        print(f"Iterations: {result['iterations']}")
        print(f"Cycles: {result['cycles']}")
    elif args.loop:
        result = run_dry_run_orchestration_loop(
            iterations=args.iterations,
            audit_log_dir=args.audit_log_dir,
            fail_sell_attempts=args.fail_sell_attempts,
        )
        print("=== DRY RUN ORCHESTRATION LOOP COMPLETE ===")
        print(f"Audit Log: {result['audit_log_path']}")
        print(f"Iterations: {result['iterations']}")
        print(f"Cycles: {result['cycles']}")
    else:
        result = run_dry_run_demo(audit_log_dir=args.audit_log_dir, fail_sell_once=args.fail_sell_once)
        print("=== DRY RUN FLOW DEMO COMPLETE ===")
        print(f"Audit Log: {result['audit_log_path']}")
        print(f"Buy Result: {result['buy_result']}")
        print(f"Sell Retry Result: {result['sell_result']}")
