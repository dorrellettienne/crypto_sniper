import argparse
from dataclasses import asdict
import time
from typing import Any

from src.live.audit_logger import append_audit_event, build_audit_log_path
from src.live.dry_run_execution_adapter import DryRunExecutionAdapter
from src.live.idempotency import (
    FileBackedIdempotencyStore,
    InMemoryIdempotencyStore,
    build_client_order_id,
    build_request_fingerprint,
)
from src.live.interfaces import SignalProvider, TradeSignal
from src.live.prelive_orchestrator import execute_buy_with_controls, execute_sell_with_retry
from src.live.prelive_risk_engine import PreLiveRiskEngine
from src.live.path_security import ensure_dir_within_base, ensure_path_within_base
from src.live.policy_profiles import (
    DEFAULT_POLICY_PROFILE_NAME,
    DEFAULT_POLICY_PROFILES_PATH,
    get_policy_profile,
)
from src.live.signal_provider_file import FileSignalProvider
from src.live.signal_provider_dexscreener import DexScreenerSignalProvider
from src.live.signal_provider_stub import StubSignalProvider
from src.live.token_safety_filter import TokenSafetyFilter
from src.live.token_safety_profiles import (
    DEFAULT_TOKEN_SAFETY_PROFILE_NAME,
    DEFAULT_TOKEN_SAFETY_PROFILES_PATH,
    get_token_safety_profile,
)
from src.live.prelive_service_rollup_export import (
    build_service_rollup_export_csv_path,
    build_service_rollup_export_json_path,
    save_service_rollup_csv,
    save_service_rollup_json,
)
from src.runner.paper_sim_candidate_runner import (
    DEFAULT_CANDIDATE_PRESET_NAME,
    DEFAULT_CANDIDATE_PRESETS_PATH,
    get_candidate_preset,
)


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


def _copy_rollup(rollup: dict[str, Any]) -> dict[str, Any]:
    return dict(rollup)


def _split_csv_values(text: str | None) -> list[str]:
    if not text:
        return []
    return [part.strip() for part in str(text).split(",") if part.strip()]


def _validate_service_startup_args(
    use_stub_signals: bool,
    signals_file_path: str,
    use_dexscreener_signals: bool = False,
) -> None:
    selected = 0
    selected += 1 if use_stub_signals else 0
    selected += 1 if bool(signals_file_path) else 0
    selected += 1 if use_dexscreener_signals else 0
    if selected > 1:
        raise ValueError(
            "use only one signal source: --use-stub-signals or --signals-file-path or --use-dexscreener-signals"
        )


def _build_signal_provider_from_args(args, dexscreener_fetcher=None):
    stub_count = args.max_iterations if args.max_iterations and args.max_iterations > 0 else 10
    if args.signals_file_path:
        return FileSignalProvider.from_path(args.signals_file_path)
    if getattr(args, "use_dexscreener_signals", False):
        if dexscreener_fetcher is None:
            dexscreener_fetcher = lambda: {"pairs": []}
        return DexScreenerSignalProvider(
            dexscreener_fetcher,
            default_usd_size=100.0,
            chain_id=(args.dexscreener_chain_id or None),
            min_liquidity_usd=args.dexscreener_min_liquidity_usd,
            max_pair_age_seconds=args.dexscreener_max_pair_age_seconds,
        )
    if args.use_stub_signals:
        return StubSignalProvider(_default_stub_signals(stub_count))
    return StubSignalProvider([])


def _rollup_export_payload(
    loop_name: str,
    iteration: int,
    rollup: dict[str, Any],
    candidate_preset_name: str | None,
    policy_profile_name: str | None,
) -> dict[str, Any]:
    return {
        "loop_name": loop_name,
        "iteration": iteration,
        "candidate_preset_name": candidate_preset_name,
        "policy_profile_name": policy_profile_name,
        "rollup": _copy_rollup(rollup),
    }


def run_prelive_service_loop(
    signal_provider: SignalProvider,
    max_iterations: int | None = 10,
    audit_log_dir: str = "data/exports",
    loop_name: str = "prelive_service",
    rollup_emit_every: int = 0,
    interval_seconds: float = 0.0,
    continue_on_cycle_error: bool = True,
    use_candidate_preset: bool = False,
    candidate_preset_name: str = DEFAULT_CANDIDATE_PRESET_NAME,
    candidate_presets_path: str = DEFAULT_CANDIDATE_PRESETS_PATH,
    token_allowlist: list[str] | None = None,
    token_blocklist: list[str] | None = None,
    symbol_allowlist: list[str] | None = None,
    min_usd_size: float | None = None,
    max_usd_size: float | None = None,
    token_cooldown_calls: int = 0,
    safety_token_allowlist: list[str] | None = None,
    safety_token_blocklist: list[str] | None = None,
    safety_min_token_age_seconds: float | None = None,
    safety_min_liquidity_usd: float | None = None,
    use_token_safety_profile: bool = False,
    token_safety_profile_name: str = DEFAULT_TOKEN_SAFETY_PROFILE_NAME,
    token_safety_profiles_path: str = DEFAULT_TOKEN_SAFETY_PROFILES_PATH,
    use_policy_profile: bool = False,
    policy_profile_name: str = DEFAULT_POLICY_PROFILE_NAME,
    policy_profiles_path: str = DEFAULT_POLICY_PROFILES_PATH,
    rollup_export_json_dir: str | None = None,
    rollup_export_csv_dir: str | None = None,
    idempotency_store_path: str | None = None,
    sleep_fn=None,
) -> dict[str, Any]:
    if max_iterations is not None and max_iterations <= 0:
        raise ValueError("max_iterations must be > 0")
    if rollup_emit_every < 0:
        raise ValueError("rollup_emit_every must be >= 0")
    if interval_seconds < 0:
        raise ValueError("interval_seconds must be >= 0")

    if sleep_fn is None:
        sleep_fn = time.sleep

    audit_log_path = build_audit_log_path(audit_log_dir, prefix=loop_name)
    adapter = DryRunExecutionAdapter()
    token_safety_profile = None
    if use_token_safety_profile:
        token_safety_profile = get_token_safety_profile(
            profile_name=token_safety_profile_name,
            profiles_path=token_safety_profiles_path,
        )
        if safety_token_allowlist is None:
            safety_token_allowlist = list(token_safety_profile.get("token_allowlist") or [])
        if safety_token_blocklist is None:
            safety_token_blocklist = list(token_safety_profile.get("token_blocklist") or [])
        if safety_min_token_age_seconds is None:
            safety_min_token_age_seconds = token_safety_profile.get("min_token_age_seconds")
        if safety_min_liquidity_usd is None:
            safety_min_liquidity_usd = token_safety_profile.get("min_liquidity_usd")

    policy_profile = None
    if use_policy_profile:
        policy_profile = get_policy_profile(
            profile_name=policy_profile_name,
            profiles_path=policy_profiles_path,
        )
        if token_allowlist is None:
            token_allowlist = list(policy_profile.get("token_allowlist") or [])
        if token_blocklist is None:
            token_blocklist = list(policy_profile.get("token_blocklist") or [])
        if symbol_allowlist is None:
            symbol_allowlist = list(policy_profile.get("symbol_allowlist") or [])
        if min_usd_size is None:
            min_usd_size = policy_profile.get("min_usd_size")
        if max_usd_size is None:
            max_usd_size = policy_profile.get("max_usd_size")
        if token_cooldown_calls == 0:
            token_cooldown_calls = int(policy_profile.get("token_cooldown_calls", 0))

    risk_engine = PreLiveRiskEngine(
        token_allowlist=token_allowlist,
        token_blocklist=token_blocklist,
        symbol_allowlist=symbol_allowlist,
        min_usd_size=min_usd_size,
        max_usd_size=max_usd_size,
        token_cooldown_calls=token_cooldown_calls,
    )
    safety_filter = TokenSafetyFilter(
        token_allowlist=safety_token_allowlist,
        token_blocklist=safety_token_blocklist,
        min_token_age_seconds=safety_min_token_age_seconds,
        min_liquidity_usd=safety_min_liquidity_usd,
    )
    candidate_preset = None
    configured_usd_size = None
    configured_exit_price = 0.02
    if use_candidate_preset:
        candidate_preset = get_candidate_preset(
            preset_name=candidate_preset_name,
            presets_path=candidate_presets_path,
        )
        configured_usd_size = float(candidate_preset.get("usd_size", 100.0))
        configured_exit_price = float(candidate_preset.get("sell_price", 0.02))

    rollup = {
        "iterations": 0,
        "signals_seen": 0,
        "signals_missing": 0,
        "risk_allowed": 0,
        "risk_blocked": 0,
        "safety_allowed": 0,
        "safety_blocked": 0,
        "buy_ok": 0,
        "buy_failed": 0,
        "sell_ok": 0,
        "sell_failed": 0,
        "sell_retry_events": 0,
        "max_sell_attempts": 0,
        "service_errors": 0,
        "interrupted": False,
        "idempotency_suppressed": 0,
    }
    idempotency_store = (
        FileBackedIdempotencyStore(idempotency_store_path)
        if idempotency_store_path
        else InMemoryIdempotencyStore()
    )

    append_audit_event(
        audit_log_path,
        "service_started",
        {
            "max_iterations": max_iterations,
            "loop_name": loop_name,
            "rollup_emit_every": rollup_emit_every,
            "interval_seconds": interval_seconds,
            "continue_on_cycle_error": continue_on_cycle_error,
            "candidate_preset_name": candidate_preset.get("name") if candidate_preset else None,
            "candidate_presets_path": candidate_presets_path if candidate_preset else None,
            "policy_profile_name": policy_profile.get("name") if policy_profile else None,
            "policy_profiles_path": policy_profiles_path if policy_profile else None,
            "token_safety_profile_name": token_safety_profile.get("name") if token_safety_profile else None,
            "token_safety_profiles_path": token_safety_profiles_path if token_safety_profile else None,
            "policy": {
                "token_allowlist": list(token_allowlist or []),
                "token_blocklist": list(token_blocklist or []),
                "symbol_allowlist": list(symbol_allowlist or []),
                "min_usd_size": min_usd_size,
                "max_usd_size": max_usd_size,
                "token_cooldown_calls": token_cooldown_calls,
            },
            "safety_filter": {
                "token_allowlist": list(safety_token_allowlist or []),
                "token_blocklist": list(safety_token_blocklist or []),
                "min_token_age_seconds": safety_min_token_age_seconds,
                "min_liquidity_usd": safety_min_liquidity_usd,
            },
            "signal_source": {
                "provider_class": signal_provider.__class__.__name__,
            },
            "idempotency": {
                "store_type": "file" if idempotency_store_path else "memory",
                "store_path": idempotency_store_path,
            },
        },
    )

    i = 0
    try:
        while max_iterations is None or i < max_iterations:
            i += 1
            rollup["iterations"] += 1
            try:
                signal = signal_provider.get_next_signal()
                if signal is None:
                    rollup["signals_missing"] += 1
                    append_audit_event(audit_log_path, "service_no_signal", {"iteration": i})
                else:
                    if configured_usd_size is not None:
                        signal = TradeSignal(
                            token_address=signal.token_address,
                            symbol=signal.symbol,
                            entry_price=signal.entry_price,
                            usd_size=configured_usd_size,
                            metadata=signal.metadata,
                        )

                    rollup["signals_seen"] += 1
                    append_audit_event(audit_log_path, "service_signal_received", {"iteration": i, "signal": asdict(signal)})

                    safety_assessment = safety_filter.assess(signal)
                    append_audit_event(
                        audit_log_path,
                        "token_safety_decision",
                        {
                            "iteration": i,
                            "allowed": safety_assessment.allowed,
                            "reason": safety_assessment.primary_reason,
                            "reasons": list(safety_assessment.reasons or []),
                            "score": safety_assessment.score,
                            "details": safety_assessment.details or {},
                        },
                    )
                    if not safety_assessment.allowed:
                        rollup["safety_blocked"] += 1
                        append_audit_event(audit_log_path, "service_cycle_completed", {"iteration": i, "status": "safety_blocked"})
                        continue
                    rollup["safety_allowed"] += 1

                    request_key = build_request_fingerprint(
                        action="buy",
                        token_address=signal.token_address,
                        symbol=signal.symbol,
                        entry_price=signal.entry_price,
                        usd_size=signal.usd_size,
                    )
                    idem = idempotency_store.decide_once(request_key)
                    client_order_id = build_client_order_id(
                        action="buy",
                        token_address=signal.token_address,
                        symbol=signal.symbol,
                        entry_price=signal.entry_price,
                        usd_size=signal.usd_size,
                        sequence=i,
                    )
                    append_audit_event(
                        audit_log_path,
                        "client_order_id_assigned",
                        {"iteration": i, "action": "buy", "client_order_id": client_order_id, "request_key": request_key},
                    )
                    if not idem.allowed:
                        rollup["idempotency_suppressed"] += 1
                        append_audit_event(
                            audit_log_path,
                            "idempotency_duplicate_suppressed",
                            {"iteration": i, "action": "buy", "request_key": request_key, "reason": idem.reason},
                        )
                        append_audit_event(audit_log_path, "service_cycle_completed", {"iteration": i, "status": "duplicate_suppressed"})
                        continue

                    buy_result = execute_buy_with_controls(
                        adapter=adapter,
                        risk_engine=risk_engine,
                        audit_log_path=audit_log_path,
                        token_address=signal.token_address,
                        symbol=signal.symbol,
                        entry_price=signal.entry_price,
                        usd_size=signal.usd_size,
                        client_order_id=client_order_id,
                    )

                    if buy_result.get("risk_allowed"):
                        rollup["risk_allowed"] += 1
                    else:
                        rollup["risk_blocked"] += 1
                        append_audit_event(audit_log_path, "service_cycle_completed", {"iteration": i, "status": "risk_blocked"})
                        buy_result = None

                    if buy_result:
                        if buy_result.get("ok"):
                            rollup["buy_ok"] += 1
                        else:
                            rollup["buy_failed"] += 1
                            append_audit_event(audit_log_path, "service_cycle_completed", {"iteration": i, "status": "buy_failed"})
                            buy_result = None

                    if buy_result:
                        position_id = getattr(buy_result.get("execution"), "position_id", None) or 1
                        sell_result = execute_sell_with_retry(
                            adapter=adapter,
                            audit_log_path=audit_log_path,
                            position_id=int(position_id),
                            exit_price=configured_exit_price,
                            max_attempts=3,
                            client_order_id=build_client_order_id(
                                action="sell",
                                token_address=signal.token_address,
                                symbol=signal.symbol,
                                entry_price=signal.entry_price,
                                usd_size=signal.usd_size,
                                sequence=i,
                            ),
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
                            {"iteration": i, "status": cycle_status, "sell_attempts": sell_result.get("attempts", 0)},
                        )
            except KeyboardInterrupt:
                rollup["interrupted"] = True
                append_audit_event(audit_log_path, "service_interrupted", {"iteration": i})
                break
            except Exception as exc:
                rollup["service_errors"] += 1
                append_audit_event(audit_log_path, "service_error", {"iteration": i, "error": str(exc)})
                if not continue_on_cycle_error:
                    raise

            if rollup_emit_every and rollup["iterations"] % rollup_emit_every == 0:
                rollup_payload = _rollup_export_payload(
                    loop_name=loop_name,
                    iteration=rollup["iterations"],
                    rollup=rollup,
                    candidate_preset_name=candidate_preset.get("name") if candidate_preset else None,
                    policy_profile_name=policy_profile.get("name") if policy_profile else None,
                )
                append_audit_event(
                    audit_log_path,
                    "service_rollup",
                    rollup_payload,
                )
                if rollup_export_json_dir:
                    save_service_rollup_json(
                        rollup_payload,
                        build_service_rollup_export_json_path(
                            rollup_export_json_dir,
                            prefix=f"{loop_name}_rollup_iter{rollup['iterations']}",
                        ),
                    )
                if rollup_export_csv_dir:
                    save_service_rollup_csv(
                        rollup_payload,
                        build_service_rollup_export_csv_path(
                            rollup_export_csv_dir,
                            prefix=f"{loop_name}_rollup_iter{rollup['iterations']}",
                        ),
                    )

            if interval_seconds > 0 and (max_iterations is None or i < max_iterations):
                sleep_fn(interval_seconds)
    except KeyboardInterrupt:
        rollup["interrupted"] = True
        append_audit_event(audit_log_path, "service_interrupted", {"iteration": rollup["iterations"]})

    final_rollup_payload = _rollup_export_payload(
        loop_name=loop_name,
        iteration=rollup["iterations"],
        rollup=rollup,
        candidate_preset_name=candidate_preset.get("name") if candidate_preset else None,
        policy_profile_name=policy_profile.get("name") if policy_profile else None,
    )
    append_audit_event(
        audit_log_path,
        "service_completed",
        final_rollup_payload,
    )
    final_rollup_json_path = None
    final_rollup_csv_path = None
    if rollup_export_json_dir:
        final_rollup_json_path = save_service_rollup_json(
            final_rollup_payload,
            build_service_rollup_export_json_path(rollup_export_json_dir, prefix=f"{loop_name}_rollup_final"),
        )
    if rollup_export_csv_dir:
        final_rollup_csv_path = save_service_rollup_csv(
            final_rollup_payload,
            build_service_rollup_export_csv_path(rollup_export_csv_dir, prefix=f"{loop_name}_rollup_final"),
        )
    return {
        "audit_log_path": audit_log_path,
        "rollup": rollup,
        "final_rollup_json_path": final_rollup_json_path,
        "final_rollup_csv_path": final_rollup_csv_path,
        "policy_profile": policy_profile,
        "token_safety_profile": token_safety_profile,
        "idempotency_store_path": idempotency_store_path,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--audit-log-dir", type=str, default="data/exports")
    parser.add_argument("--use-stub-signals", action="store_true")
    parser.add_argument("--signals-file-path", type=str, default="")
    parser.add_argument("--use-dexscreener-signals", action="store_true")
    parser.add_argument("--dexscreener-chain-id", type=str, default="")
    parser.add_argument("--dexscreener-min-liquidity-usd", type=float, default=None)
    parser.add_argument("--dexscreener-max-pair-age-seconds", type=float, default=None)
    parser.add_argument("--rollup-emit-every", type=int, default=0)
    parser.add_argument("--interval-seconds", type=float, default=0.0)
    parser.add_argument("--stop-on-cycle-error", action="store_true")
    parser.add_argument("--use-candidate-preset", action="store_true")
    parser.add_argument("--candidate-preset-name", type=str, default=DEFAULT_CANDIDATE_PRESET_NAME)
    parser.add_argument("--candidate-presets-json-path", type=str, default=DEFAULT_CANDIDATE_PRESETS_PATH)
    parser.add_argument("--use-policy-profile", action="store_true")
    parser.add_argument("--policy-profile-name", type=str, default=DEFAULT_POLICY_PROFILE_NAME)
    parser.add_argument("--policy-profiles-json-path", type=str, default=DEFAULT_POLICY_PROFILES_PATH)
    parser.add_argument("--token-allowlist", type=str, default="")
    parser.add_argument("--token-blocklist", type=str, default="")
    parser.add_argument("--symbol-allowlist", type=str, default="")
    parser.add_argument("--min-usd-size", type=float, default=None)
    parser.add_argument("--max-usd-size", type=float, default=None)
    parser.add_argument("--token-cooldown-calls", type=int, default=0)
    parser.add_argument("--safety-token-allowlist", type=str, default="")
    parser.add_argument("--safety-token-blocklist", type=str, default="")
    parser.add_argument("--safety-min-token-age-seconds", type=float, default=None)
    parser.add_argument("--safety-min-liquidity-usd", type=float, default=None)
    parser.add_argument("--use-token-safety-profile", action="store_true")
    parser.add_argument("--token-safety-profile-name", type=str, default=DEFAULT_TOKEN_SAFETY_PROFILE_NAME)
    parser.add_argument("--token-safety-profiles-json-path", type=str, default=DEFAULT_TOKEN_SAFETY_PROFILES_PATH)
    parser.add_argument("--rollup-export-json-dir", type=str, default=None)
    parser.add_argument("--rollup-export-csv-dir", type=str, default=None)
    parser.add_argument("--idempotency-store-path", type=str, default=None)
    parser.add_argument("--allow-unsafe-paths", action="store_true")
    args = parser.parse_args()

    _validate_service_startup_args(
        use_stub_signals=args.use_stub_signals,
        signals_file_path=args.signals_file_path,
        use_dexscreener_signals=args.use_dexscreener_signals,
    )

    if not args.allow_unsafe_paths:
        if args.audit_log_dir:
            ensure_dir_within_base(args.audit_log_dir)
        for dir_path in [args.rollup_export_json_dir, args.rollup_export_csv_dir]:
            if dir_path:
                ensure_dir_within_base(dir_path)
        if args.idempotency_store_path:
            ensure_path_within_base(args.idempotency_store_path)

    provider = _build_signal_provider_from_args(args)
    result = run_prelive_service_loop(
        signal_provider=provider,
        max_iterations=None if args.continuous else args.max_iterations,
        audit_log_dir=args.audit_log_dir,
        rollup_emit_every=args.rollup_emit_every,
        interval_seconds=args.interval_seconds,
        continue_on_cycle_error=not args.stop_on_cycle_error,
        use_candidate_preset=args.use_candidate_preset,
        candidate_preset_name=args.candidate_preset_name,
        candidate_presets_path=args.candidate_presets_json_path,
        use_policy_profile=args.use_policy_profile,
        policy_profile_name=args.policy_profile_name,
        policy_profiles_path=args.policy_profiles_json_path,
        token_allowlist=_split_csv_values(args.token_allowlist),
        token_blocklist=_split_csv_values(args.token_blocklist),
        symbol_allowlist=_split_csv_values(args.symbol_allowlist),
        min_usd_size=args.min_usd_size,
        max_usd_size=args.max_usd_size,
        token_cooldown_calls=args.token_cooldown_calls,
        safety_token_allowlist=_split_csv_values(args.safety_token_allowlist),
        safety_token_blocklist=_split_csv_values(args.safety_token_blocklist),
        safety_min_token_age_seconds=args.safety_min_token_age_seconds,
        safety_min_liquidity_usd=args.safety_min_liquidity_usd,
        use_token_safety_profile=args.use_token_safety_profile,
        token_safety_profile_name=args.token_safety_profile_name,
        token_safety_profiles_path=args.token_safety_profiles_json_path,
        rollup_export_json_dir=args.rollup_export_json_dir,
        rollup_export_csv_dir=args.rollup_export_csv_dir,
        idempotency_store_path=args.idempotency_store_path,
    )
    print("=== PRELIVE SERVICE LOOP COMPLETE ===")
    print(f"Audit Log: {result['audit_log_path']}")
    print(f"Rollup: {result['rollup']}")
    if result.get("final_rollup_json_path"):
        print(f"Final Rollup JSON: {result['final_rollup_json_path']}")
    if result.get("final_rollup_csv_path"):
        print(f"Final Rollup CSV: {result['final_rollup_csv_path']}")
