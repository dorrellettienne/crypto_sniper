import argparse
from dataclasses import asdict
from typing import Any

from src.live.audit_logger import append_audit_event, build_audit_log_path
from src.live.dry_run_execution_adapter import DryRunExecutionAdapter
from src.live.interfaces import SignalProvider, TradeSignal
from src.live.prelive_orchestrator import execute_buy_with_controls, execute_sell_with_retry
from src.live.prelive_risk_engine import PreLiveRiskEngine
from src.live.signal_provider_stub import StubSignalProvider


def _default_stub_signals(iterations: int) -> list[TradeSignal]:
    signals = []
    for i in range(iterations):
        signals.append(
            TradeSignal(
                token_address=f"STUB_TOKEN_{i}",
                symbol="STUB",
                entry_price=0.01,
                usd_size=100.0,
                metadata={"index": i},
            )
        )
    return signals


def run_prelive_service_loop(
    signal_provider: SignalProvider,
    max_iterations: int = 10,
    audit_log_dir: str = "data/exports",
    loop_name: str = "prelive_service",
) -> dict[str, Any]:
    if max_iterations <= 0:
        raise ValueError("max_iterations must be > 0")

    audit_log_path = build_audit_log_path(audit_log_dir, prefix=loop_name)
    adapter = DryRunExecutionAdapter()
    risk_engine = PreLiveRiskEngine()

    rollup = {
        "iterations": 0,
        "signals_seen": 0,
        "signals_missing": 0,
        "risk_allowed": 0,
        "risk_blocked": 0,
        "buy_ok": 0,
        "buy_failed": 0,
        "sell_ok": 0,
        "sell_failed": 0,
        "sell_retry_events": 0,
        "max_sell_attempts": 0,
    }

    append_audit_event(audit_log_path, "service_started", {"max_iterations": max_iterations, "loop_name": loop_name})

    for i in range(max_iterations):
        rollup["iterations"] += 1
        signal = signal_provider.get_next_signal()
        if signal is None:
            rollup["signals_missing"] += 1
            append_audit_event(audit_log_path, "service_no_signal", {"iteration": i + 1})
            continue

        rollup["signals_seen"] += 1
        append_audit_event(audit_log_path, "service_signal_received", {"iteration": i + 1, "signal": asdict(signal)})

        buy_result = execute_buy_with_controls(
            adapter=adapter,
            risk_engine=risk_engine,
            audit_log_path=audit_log_path,
            token_address=signal.token_address,
            symbol=signal.symbol,
            entry_price=signal.entry_price,
            usd_size=signal.usd_size,
        )

        if buy_result.get("risk_allowed"):
            rollup["risk_allowed"] += 1
        else:
            rollup["risk_blocked"] += 1
            append_audit_event(audit_log_path, "service_cycle_completed", {"iteration": i + 1, "status": "risk_blocked"})
            continue

        if buy_result.get("ok"):
            rollup["buy_ok"] += 1
        else:
            rollup["buy_failed"] += 1
            append_audit_event(audit_log_path, "service_cycle_completed", {"iteration": i + 1, "status": "buy_failed"})
            continue

        position_id = getattr(buy_result.get("execution"), "position_id", None) or 1
        sell_result = execute_sell_with_retry(
            adapter=adapter,
            audit_log_path=audit_log_path,
            position_id=int(position_id),
            exit_price=0.02,
            max_attempts=3,
        )

        rollup["sell_retry_events"] += 1
        rollup["max_sell_attempts"] = max(rollup["max_sell_attempts"], int(sell_result.get("attempts", 0)))
        if sell_result.get("ok"):
            rollup["sell_ok"] += 1
            cycle_status = "ok"
        else:
            rollup["sell_failed"] += 1
            cycle_status = "sell_failed"

        append_audit_event(
            audit_log_path,
            "service_cycle_completed",
            {"iteration": i + 1, "status": cycle_status, "sell_attempts": sell_result.get("attempts", 0)},
        )

    append_audit_event(audit_log_path, "service_completed", {"rollup": rollup})
    return {"audit_log_path": audit_log_path, "rollup": rollup}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--audit-log-dir", type=str, default="data/exports")
    parser.add_argument("--use-stub-signals", action="store_true")
    args = parser.parse_args()

    provider = StubSignalProvider(_default_stub_signals(args.max_iterations)) if args.use_stub_signals else StubSignalProvider([])
    result = run_prelive_service_loop(
        signal_provider=provider,
        max_iterations=args.max_iterations,
        audit_log_dir=args.audit_log_dir,
    )
    print("=== PRELIVE SERVICE LOOP COMPLETE ===")
    print(f"Audit Log: {result['audit_log_path']}")
    print(f"Rollup: {result['rollup']}")
