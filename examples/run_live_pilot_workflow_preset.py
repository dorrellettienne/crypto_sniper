#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORTS_DIR = REPO_ROOT / "data" / "exports"


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    print("$", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
    return int(proc.returncode)


def _run_or_raise(cmd: list[str], *, env: dict[str, str] | None = None, error: str) -> None:
    code = _run(cmd, env=env)
    if code != 0:
        raise RuntimeError(error)


def _run_or_raise_retry(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    error: str,
    attempts: int,
    initial_delay_seconds: float,
    backoff_seconds: float,
) -> None:
    last_code = 0
    attempt_count = max(1, int(attempts))
    for idx in range(attempt_count):
        code = _run(cmd, env=env)
        if code == 0:
            return
        last_code = code
        if idx >= (attempt_count - 1):
            break
        sleep_s = max(0.0, float(initial_delay_seconds) + (float(backoff_seconds) * idx))
        print(f"retrying_command_after_failure attempt={idx + 2}/{attempt_count} sleep_seconds={sleep_s}")
        time.sleep(sleep_s)
    raise RuntimeError(f"{error}: exit={last_code}")


def _python_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    return env


def _has_signer_secret_env() -> bool:
    key_b58 = str(os.environ.get("SOLANA_PILOT_PRIVATE_KEY_B58", "")).strip()
    keypair_path = str(os.environ.get("SOLANA_PILOT_KEYPAIR_JSON_PATH", "")).strip()
    if key_b58:
        return True
    if keypair_path and Path(keypair_path).exists():
        return True
    return False


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _get_signer_pubkey() -> str:
    cmd = [sys.executable, r".\examples\print_solana_signer_pubkey_from_env.py"]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=_python_env(),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"signer_pubkey_helper_failed: exit={proc.returncode}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("signer_pubkey_helper_no_output")
    payload = json.loads(out)
    if not payload.get("ok"):
        raise RuntimeError(f"signer_pubkey_helper_failed: {payload}")
    return str(payload["pubkey"])


def _sync_live_config(config_path: Path, usd_size: float, signer_pubkey: str) -> None:
    if not config_path.exists():
        raise RuntimeError(f"config_not_found: {config_path}")
    cfg = _load_json(config_path)
    cfg["wallet_public_key"] = signer_pubkey
    cfg["live_send_network_enabled"] = True
    cfg["dex_quote_only_mode"] = False
    cfg["max_order_usd_cap"] = float(usd_size)
    cfg["pilot_hard_max_order_usd_cap"] = float(usd_size)
    auto_exit_enabled = bool(cfg.get("live_auto_exit_enabled", False))
    if auto_exit_enabled:
        cfg["manual_submit_mode"] = "buy_and_sell"
        cfg["live_send_max_notional_usd_total"] = ""
        cfg["live_send_max_orders_per_session"] = 2
    else:
        cfg["live_send_max_notional_usd_total"] = float(usd_size)
        cfg["live_send_max_orders_per_session"] = 1
    cfg["live_send_chain_reconciliation_fetch_attempts"] = 12
    cfg["live_send_chain_reconciliation_fetch_poll_interval_seconds"] = 1.5
    _save_json(config_path, cfg)


def _run_preflight_checks(config_path: Path, usd_size: float, signer_pubkey: str) -> None:
    cfg = _load_json(config_path)
    checks: list[tuple[str, bool, str]] = []

    checks.append(
        (
            "signer_secret_present",
            _has_signer_secret_env(),
            "missing signer secret env (set SOLANA_PILOT_PRIVATE_KEY_B58 or SOLANA_PILOT_KEYPAIR_JSON_PATH)",
        )
    )
    checks.append(("wallet_public_key_matches_signer", str(cfg.get("wallet_public_key", "")).strip() == signer_pubkey, "wallet_public_key mismatch"))
    checks.append(("live_send_network_enabled", bool(cfg.get("live_send_network_enabled")) is True, "live_send_network_enabled must be true"))
    checks.append(("dex_quote_only_mode_false", bool(cfg.get("dex_quote_only_mode")) is False, "dex_quote_only_mode must be false"))
    auto_exit_enabled = bool(cfg.get("live_auto_exit_enabled", False))
    expected_orders = 2 if auto_exit_enabled else 1
    expected_notional_cap = float(usd_size)
    checks.append(("max_order_usd_cap", float(cfg.get("max_order_usd_cap", -1)) <= usd_size, "max_order_usd_cap too high"))
    checks.append(("pilot_hard_max_order_usd_cap", float(cfg.get("pilot_hard_max_order_usd_cap", -1)) <= usd_size, "pilot_hard_max_order_usd_cap too high"))
    if auto_exit_enabled:
        raw_cap = cfg.get("live_send_max_notional_usd_total")
        if raw_cap not in (None, ""):
            checks.append(("live_send_max_notional_usd_total", float(raw_cap) <= expected_notional_cap * 2.0, "live_send_max_notional_usd_total too high"))
    else:
        checks.append(("live_send_max_notional_usd_total", float(cfg.get("live_send_max_notional_usd_total", -1)) <= expected_notional_cap, "live_send_max_notional_usd_total too high"))
    checks.append(("live_send_max_orders_per_session", int(cfg.get("live_send_max_orders_per_session", -1)) == expected_orders, f"live_send_max_orders_per_session must be {expected_orders}"))
    if auto_exit_enabled:
        checks.append(("manual_submit_mode", str(cfg.get("manual_submit_mode", "")).strip().lower() in {"buy_and_sell", "buy_sell", "all"}, "manual_submit_mode must allow sell for auto-exit"))

    failed = [name for name, ok, _ in checks if not ok]
    if failed:
        raise RuntimeError(f"env_preflight_failed: {failed}")


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def _write_v16_release_cycle_incident(stage: str, exc: Exception, exports_dir: Path) -> None:
    payload = {
        "ok": True,
        "report_version": "v16_release_cycle_incident_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "status": "failed",
            "stage": str(stage),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        },
    }
    out_json = exports_dir / "v16_release_cycle_incident.json"
    out_md = exports_dir / "v16_release_cycle_incident.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    out_md.write_text(
        "\n".join(
            [
                "# V16 Release Cycle Incident",
                "",
                f"- generated_at_utc: `{payload.get('generated_at_utc')}`",
                f"- stage: `{stage}`",
                f"- error_type: `{type(exc).__name__}`",
                f"- error_message: `{str(exc)}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"v16_release_incident_json={out_json}")


def _dedupe_promoted_candidates(promoted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_token: dict[str, dict[str, Any]] = {}
    for row in promoted:
        if not isinstance(row, dict):
            continue
        tok = str(row.get("token_address") or "").strip()
        if not tok:
            continue
        prev = by_token.get(tok)
        if prev is None:
            by_token[tok] = row
            continue
        prev_score = _safe_float(prev.get("score_total"), 0.0)
        curr_score = _safe_float(row.get("score_total"), 0.0)
        if curr_score > prev_score:
            by_token[tok] = row
    rows = list(by_token.values())
    rows.sort(key=lambda r: (-_safe_float(r.get("score_total"), 0.0), str(r.get("token_address") or "")))
    return rows


def _load_v2_execution_allowlist(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"v2_live_gate_missing_execution_bridge: {path}")
    payload = _load_json(path)
    rows = [dict(x) for x in list(payload.get("execution_candidates") or []) if isinstance(x, dict)]
    allowlist: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if str(row.get("decision") or "").strip().lower() != "execute":
            continue
        tok = str(row.get("token_address") or "").strip()
        if not tok or tok in seen:
            continue
        seen.add(tok)
        allowlist.append(
            {
                "token_address": tok,
                "symbol": str(row.get("symbol") or "").strip(),
                "composite_score": _safe_float(row.get("composite_score"), 0.0),
                "usd_size": _safe_float(row.get("usd_size"), 0.0),
            }
        )
    return allowlist


def preset_status_only() -> None:
    _run_or_raise(
        [sys.executable, r".\examples\check_latest_live_submit_signature_status.py"],
        env=_python_env(),
        error="status_only_failed",
    )


def preset_v2_intelligence_report(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    signals_path = REPO_ROOT / args.v2_signals_json_path
    if not signals_path.exists():
        template_src = REPO_ROOT / "examples" / "v2_intelligence_signals_template.json"
        if template_src.exists():
            signals_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template_src, signals_path)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v2_intelligence_report.py",
            "--scored-discovery-json-path",
            str(exports_dir / "v16_scored_discovery_report.json"),
            "--signals-json-path",
            str(signals_path),
            "--output-json",
            str(exports_dir / "v2_intelligence_report.json"),
            "--output-md",
            str(exports_dir / "v2_intelligence_report.md"),
            "--min-composite-score-to-trade",
            str(args.v2_min_composite_score_to_trade),
        ],
        env=_python_env(),
        error="v2_intelligence_report_failed",
    )
    print("v2-intelligence-report: done")


def preset_v2_1_auto_signals(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v2_1_auto_signals.py",
            "--candidate-json-path",
            str(exports_dir / "v16_discovery_candidates.json"),
            "--scored-discovery-json-path",
            str(exports_dir / "v16_scored_discovery_report.json"),
            "--outcome-log-jsonl-path",
            str(exports_dir / "v16_scored_candidate_outcomes.jsonl"),
            "--deployer-history-json-path",
            str(exports_dir / "v2_deployer_history_store.json"),
            "--manual-signals-json-path",
            str(exports_dir / "v2_intelligence_signals.json"),
            "--output-json",
            str(exports_dir / "v2_intelligence_signals_auto.json"),
            "--output-md",
            str(exports_dir / "v2_intelligence_signals_auto.md"),
            "--output-merged-json",
            str(exports_dir / "v2_intelligence_signals.json"),
        ],
        env=_python_env(),
        error="v2_1_auto_signals_failed",
    )
    print("v2.1-auto-signals: done")


def preset_v2_2_deployer_history_store(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v2_2_deployer_history_store.py",
            "--signals-json-path",
            str(exports_dir / "v2_intelligence_signals.json"),
            "--auto-signals-json-path",
            str(exports_dir / "v2_intelligence_signals_auto.json"),
            "--outcome-log-jsonl-path",
            str(exports_dir / "v16_scored_candidate_outcomes.jsonl"),
            "--existing-history-json-path",
            str(exports_dir / "v2_deployer_history_store.json"),
            "--output-json",
            str(exports_dir / "v2_deployer_history_store.json"),
            "--output-md",
            str(exports_dir / "v2_deployer_history_store.md"),
        ],
        env=_python_env(),
        error="v2_2_deployer_history_store_failed",
    )
    print("v2.2-deployer-history-store: done")


def preset_v2_3_wallet_linkage_engine(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v2_3_wallet_linkage_engine.py",
            "--signals-json-path",
            str(exports_dir / "v2_intelligence_signals.json"),
            "--candidate-json-path",
            str(exports_dir / "v16_discovery_candidates.json"),
            "--output-json",
            str(exports_dir / "v2_wallet_linkage_engine.json"),
            "--output-md",
            str(exports_dir / "v2_wallet_linkage_engine.md"),
            "--output-merged-signals-json",
            str(exports_dir / "v2_intelligence_signals.json"),
        ],
        env=_python_env(),
        error="v2_3_wallet_linkage_engine_failed",
    )
    print("v2.3-wallet-linkage-engine: done")


def preset_v2_4_structural_risk_enhancements(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v2_4_structural_risk_enhancements.py",
            "--signals-json-path",
            str(exports_dir / "v2_intelligence_signals.json"),
            "--candidate-json-path",
            str(exports_dir / "v16_discovery_candidates.json"),
            "--output-json",
            str(exports_dir / "v2_structural_risk_enhancements.json"),
            "--output-md",
            str(exports_dir / "v2_structural_risk_enhancements.md"),
            "--output-merged-signals-json",
            str(exports_dir / "v2_intelligence_signals.json"),
        ],
        env=_python_env(),
        error="v2_4_structural_risk_enhancements_failed",
    )
    print("v2.4-structural-risk-enhancements: done")


def preset_v2_5_execution_bridge(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v2_5_execution_bridge.py",
            "--v2-intelligence-report-json-path",
            str(exports_dir / "v2_intelligence_report.json"),
            "--base-usd-size",
            str(args.usd_size),
            "--max-executable-candidates",
            str(args.v2_max_executable_candidates),
            "--output-json",
            str(exports_dir / "v2_execution_bridge.json"),
            "--output-md",
            str(exports_dir / "v2_execution_bridge.md"),
        ],
        env=_python_env(),
        error="v2_5_execution_bridge_failed",
    )
    print("v2.5-execution-bridge: done")


def preset_v2_6_calibration_loop(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v2_6_calibration_loop.py",
            "--v2-execution-bridge-json-path",
            str(exports_dir / "v2_execution_bridge.json"),
            "--v2-intelligence-report-json-path",
            str(exports_dir / "v2_intelligence_report.json"),
            "--outcome-log-jsonl-path",
            str(exports_dir / "v16_scored_candidate_outcomes.jsonl"),
            "--output-json",
            str(exports_dir / "v2_calibration_loop.json"),
            "--output-md",
            str(exports_dir / "v2_calibration_loop.md"),
        ],
        env=_python_env(),
        error="v2_6_calibration_loop_failed",
    )
    print("v2.6-calibration-loop: done")


def preset_v2_8_acceptance_packet(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v2_8_acceptance_packet.py",
            "--v16-performance-summary-json-path",
            str(exports_dir / "v16_performance_summary.json"),
            "--v16-go-no-go-json-path",
            str(exports_dir / "v16_go_no_go_gate.json"),
            "--v2-intelligence-report-json-path",
            str(exports_dir / "v2_intelligence_report.json"),
            "--v2-execution-bridge-json-path",
            str(exports_dir / "v2_execution_bridge.json"),
            "--v2-calibration-loop-json-path",
            str(exports_dir / "v2_calibration_loop.json"),
            "--output-json",
            str(exports_dir / "v2_acceptance_packet.json"),
            "--output-md",
            str(exports_dir / "v2_acceptance_packet.md"),
        ],
        env=_python_env(),
        error="v2_8_acceptance_packet_failed",
    )
    print("v2.8-acceptance-packet: done")


def preset_v2_9_security_preflight(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v2_9_security_preflight.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v2_security_preflight.json"),
            "--output-md",
            str(exports_dir / "v2_security_preflight.md"),
        ],
        env=_python_env(),
        error="v2_9_security_preflight_failed",
    )
    print("v2.9-security-preflight: done")


def preset_v2_10_closeout_packet(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v2_10_closeout_packet.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v2_closeout_packet.json"),
            "--output-md",
            str(exports_dir / "v2_closeout_packet.md"),
        ],
        env=_python_env(),
        error="v2_10_closeout_packet_failed",
    )
    print("v2.10-closeout-packet: done")


def preset_v2_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v2_1_auto_signals(args)
    preset_v2_2_deployer_history_store(args)
    preset_v2_3_wallet_linkage_engine(args)
    preset_v2_4_structural_risk_enhancements(args)
    preset_v2_intelligence_report(args)
    preset_v2_5_execution_bridge(args)
    preset_v2_6_calibration_loop(args)
    preset_v2_8_acceptance_packet(args)
    if not args.skip_v2_security_preflight:
        preset_v2_9_security_preflight(args)
    else:
        print("v2.9-security-preflight: skipped")
    preset_v2_10_closeout_packet(args)
    print("v2-milestone-cycle: done")


def preset_v16_supervised_discovery_live(args: argparse.Namespace) -> None:
    if not _has_signer_secret_env():
        raise RuntimeError("missing_env: require SOLANA_PILOT_PRIVATE_KEY_B58 or SOLANA_PILOT_KEYPAIR_JSON_PATH")
    if not args.skip_v2_security_preflight:
        preset_v2_9_security_preflight(args)

    config_path = REPO_ROOT / args.config_path
    signer_pubkey = _get_signer_pubkey()
    _sync_live_config(config_path, args.usd_size, signer_pubkey)
    _run_preflight_checks(config_path, args.usd_size, signer_pubkey)

    candidate_json = EXPORTS_DIR / "v16_discovery_candidates.json"
    scored_json = EXPORTS_DIR / "v16_scored_discovery_report.json"
    scored_md = EXPORTS_DIR / "v16_scored_discovery_report.md"
    selection_json = EXPORTS_DIR / "v16_selected_promoted_candidate.json"

    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_dexscreener_candidates.py",
            "--fetch-url",
            args.dexscreener_fetch_url,
            "--fallback-urls-json-path",
            args.dexscreener_fallback_urls_json_path,
            "--user-agent",
            args.dexscreener_user_agent,
            "--chain-id",
            "solana",
            "--usd-size",
            str(args.usd_size),
            "--max-candidates",
            str(args.max_fetched_candidates),
            "--output-json",
            str(candidate_json),
        ],
        env=_python_env(),
        error="v16_dexscreener_candidate_export_failed",
    )

    score_cmd = [
        sys.executable,
        r".\examples\export_scored_discovery_report.py",
        "--candidate-json-path",
        str(candidate_json),
        "--output-json",
        str(scored_json),
        "--output-md",
        str(scored_md),
        "--min-liquidity-usd",
        str(args.min_liquidity_usd),
        "--max-pair-age-seconds",
        str(args.max_pair_age_seconds),
        "--min-volume-5m-usd",
        str(args.min_volume_5m_usd),
        "--max-abs-price-change-5m-pct",
        str(args.max_abs_price_change_5m_pct),
        "--promote-max-candidates",
        str(args.promote_max_candidates),
        "--promote-min-score-total",
        str(args.promote_min_score_total),
    ]
    if args.promote_require_probe_ok:
        score_cmd.append("--promote-require-probe-ok")
    _run_or_raise(score_cmd, env=_python_env(), error="v16_scored_discovery_failed")

    scored = _load_json(scored_json)
    promoted_raw = [dict(x) for x in list(scored.get("promotion", {}).get("promoted_candidates", [])) if isinstance(x, dict)]
    promoted = _dedupe_promoted_candidates(promoted_raw)
    if not promoted:
        raise RuntimeError("v16_no_promoted_candidates_after_scoring")

    if not args.skip_v2_live_gate_refresh:
        preset_v2_1_auto_signals(args)
        preset_v2_2_deployer_history_store(args)
        preset_v2_3_wallet_linkage_engine(args)
        preset_v2_4_structural_risk_enhancements(args)
        preset_v2_intelligence_report(args)
        preset_v2_5_execution_bridge(args)

    selected: dict[str, Any]
    if args.skip_v2_live_gate_enforcement:
        selected = promoted[0]
        print("v2_live_gate=enforcement_skipped")
    else:
        allowlist = _load_v2_execution_allowlist(REPO_ROOT / args.v2_live_gate_json_path)
        if not allowlist:
            raise RuntimeError("v2_live_gate_no_executable_candidates")
        promoted_by_token = {str(c.get("token_address") or "").strip(): c for c in promoted if str(c.get("token_address") or "").strip()}
        selected = {}
        for gate_row in allowlist:
            tok = str(gate_row.get("token_address") or "").strip()
            hit = promoted_by_token.get(tok)
            if hit is not None:
                selected = hit
                break
        if not selected:
            raise RuntimeError("v2_live_gate_no_overlap_with_promoted_candidates")
        print(f"v2_live_gate_allowlist_total={len(allowlist)}")
        print(f"v2_live_gate_promoted_dedup_total={len(promoted)}")

    token_address = str(selected.get("token_address", "")).strip()
    symbol = str(selected.get("symbol", "")).strip() or "UNKNOWN"
    entry_price = float(selected.get("features", {}).get("entry_price") or 1.0)
    if entry_price <= 0:
        entry_price = 1.0
    if not token_address:
        raise RuntimeError("v16_selected_token_empty_after_v2_live_gate")
    _save_json(selection_json, selected)

    print(f"signer_pubkey={signer_pubkey}")
    print(f"selected_token={token_address}")
    print(f"selected_symbol={symbol}")
    print(f"selected_entry_price={entry_price}")
    print(f"selected_score_total={selected.get('score_total')}")
    print(f"scored_report={scored_json}")

    _run_or_raise(
        [
            sys.executable,
            "-m",
            "src.live.live_pilot_service",
            "--mode",
            "live_auto_tiny_one_trade",
            "--token-address",
            token_address,
            "--symbol",
            symbol,
            "--entry-price",
            str(entry_price),
            "--usd-size",
            str(args.usd_size),
            "--allow-unsafe-paths",
            "--adapter-config-json-path",
            args.config_path,
            "--auto-pilot-poll-interval-seconds",
            str(args.poll_interval_seconds),
            "--print-human-summary",
        ],
        env=_python_env(),
        error="v16_live_submit_failed",
    )

    if not args.skip_status_check:
        print("\nlatest_submit_status:")
        _run_or_raise(
            [sys.executable, r".\examples\check_latest_live_submit_signature_status.py"],
            env=_python_env(),
            error="v16_status_check_failed",
        )


def preset_v16_supervised_discovery_postprocess(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    v16_bundles_dir = REPO_ROOT / args.v16_bundles_dir
    if not exports_dir.exists():
        raise RuntimeError(f"exports_dir_not_found: {exports_dir}")

    print("v16-postprocess: latest status (polling for finalized if available)")
    _run_or_raise(
        [
            sys.executable,
            r".\examples\check_latest_live_submit_signature_status.py",
            "--poll-attempts",
            "6",
            "--poll-interval-seconds",
            "1",
            "--require-finalized",
            "--summary-only",
        ],
        env=_python_env(),
        error="v16_status_helper_failed",
    )
    if args.summary_only:
        return

    print("v16-postprocess: exporting latest receipt")
    receipt_cmd = [sys.executable, r".\examples\export_latest_live_submit_receipt.py"]
    if args.owner_pubkey:
        receipt_cmd.extend(["--owner-pubkey", args.owner_pubkey])
    receipt_cmd.extend(
        [
            "--output-json",
            str(exports_dir / "v16_latest_live_receipt.json"),
            "--output-md",
            str(exports_dir / "v16_latest_live_receipt.md"),
        ]
    )
    _run_or_raise_retry(
        receipt_cmd,
        env=_python_env(),
        error="v16_receipt_export_failed",
        attempts=int(args.receipt_export_retry_attempts),
        initial_delay_seconds=float(args.receipt_export_retry_initial_delay_seconds),
        backoff_seconds=float(args.receipt_export_retry_backoff_seconds),
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = v16_bundles_dir / f"v16_run_{ts}"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    _copy_if_exists(exports_dir / "v16_latest_live_receipt.json", bundle_dir / "v16_latest_live_receipt.json")
    _copy_if_exists(exports_dir / "v16_latest_live_receipt.md", bundle_dir / "v16_latest_live_receipt.md")
    _copy_if_exists(exports_dir / "v16_discovery_candidates.json", bundle_dir / "v16_discovery_candidates.json")
    _copy_if_exists(exports_dir / "v16_scored_discovery_report.json", bundle_dir / "v16_scored_discovery_report.json")
    _copy_if_exists(exports_dir / "v16_scored_discovery_report.md", bundle_dir / "v16_scored_discovery_report.md")
    _copy_if_exists(exports_dir / "v16_selected_promoted_candidate.json", bundle_dir / "v16_selected_promoted_candidate.json")

    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v16_bundles_index.py",
            "--v16-bundles-dir",
            str(v16_bundles_dir),
            "--output-json",
            str(v16_bundles_dir / "index.json"),
            "--output-md",
            str(v16_bundles_dir / "index.md"),
        ],
        env=_python_env(),
        error="v16_bundles_index_failed",
    )
    print(f"v16-bundle={bundle_dir}")
    print("v16-postprocess: done")


def preset_v16_performance_summary(args: argparse.Namespace) -> None:
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v16_performance_summary.py",
            "--v16-bundles-dir",
            args.v16_bundles_dir,
            "--max-bundles",
            "50",
            "--output-json",
            r".\data\exports\v16_performance_summary.json",
            "--output-md",
            r".\data\exports\v16_performance_summary.md",
        ],
        env=_python_env(),
        error="v16_performance_summary_failed",
    )


def preset_v16_go_no_go_gate() -> None:
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v16_go_no_go_gate.py",
            "--v16-performance-summary-json-path",
            r".\data\exports\v16_performance_summary.json",
            "--min-finalized",
            "3",
            "--max-mismatch-rate",
            "0.50",
            "--max-avg-slippage-bps",
            "50.0",
            "--max-no-finalized-streak",
            "3",
            "--output-json",
            r".\data\exports\v16_go_no_go_gate.json",
            "--output-md",
            r".\data\exports\v16_go_no_go_gate.md",
        ],
        env=_python_env(),
        error="v16_go_no_go_gate_failed",
    )


def preset_v16_release_cycle(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    stage = "start"
    try:
        stage = "v2_9_security_preflight"
        if not args.skip_v2_security_preflight:
            preset_v2_9_security_preflight(args)
        stage = "v16_supervised_discovery_live"
        preset_v16_supervised_discovery_live(args)
        stage = "v16_supervised_discovery_postprocess"
        preset_v16_supervised_discovery_postprocess(args)
        stage = "v16_performance_summary"
        preset_v16_performance_summary(args)
        stage = "v16_go_no_go_gate"
        preset_v16_go_no_go_gate()
        stage = "v2_10_closeout_packet"
        preset_v2_10_closeout_packet(args)
        print("v16-release-cycle: done")
    except Exception as exc:
        _write_v16_release_cycle_incident(stage, exc, exports_dir)
        raise RuntimeError(f"v16_release_cycle_failed_at_{stage}: {exc}") from exc


def preset_v17_supervised_exit_workflow(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    candidate_json = exports_dir / "v16_discovery_candidates.json"
    scored_json = exports_dir / "v16_scored_discovery_report.json"
    outcome_jsonl = exports_dir / "v16_scored_candidate_outcomes.jsonl"
    calib_trend_json = exports_dir / "v16_scored_candidate_calibration_trend_summary.json"
    strategy_trace_json = exports_dir / "v17_strategy_decision_trace.json"
    strategy_trace_md = exports_dir / "v17_strategy_decision_trace.md"
    strategy_trend_json = exports_dir / "v17_strategy_decision_trace_trend_summary.json"
    strategy_trend_md = exports_dir / "v17_strategy_decision_trace_trend_summary.md"
    review_json = exports_dir / "v17_strategy_decision_review_packet.json"
    review_md = exports_dir / "v17_strategy_decision_review_packet.md"

    if not candidate_json.exists():
        raise RuntimeError(f"v17_candidate_json_not_found: {candidate_json}")
    if not scored_json.exists():
        raise RuntimeError(f"v17_scored_json_not_found: {scored_json}")
    if not outcome_jsonl.exists():
        raise RuntimeError(f"v17_outcome_jsonl_not_found: {outcome_jsonl}")

    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_strategy_decision_trace.py",
            "--candidate-json-path",
            str(candidate_json),
            "--outcome-log-jsonl-path",
            str(outcome_jsonl),
            "--output-json",
            str(strategy_trace_json),
            "--output-md",
            str(strategy_trace_md),
            "--entry-adaptive-from-feedback",
            "--exit-policy-adaptive-from-feedback",
        ],
        env=_python_env(),
        error="v17_strategy_trace_failed",
    )

    _run_or_raise(
        [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            r".\examples\run_strategy_decision_feedback_postprocess.ps1",
            "-StrategyTraceJsonPath",
            str(strategy_trace_json),
            "-StrategyTraceLogJsonlPath",
            str(exports_dir / "strategy_decision_traces.jsonl"),
            "-TrendSummaryJsonPath",
            str(strategy_trend_json),
            "-TrendSummaryMdPath",
            str(strategy_trend_md),
            "-ContextRunLabel",
            "v17_supervised_exit_workflow",
        ],
        env=_python_env(),
        error="v17_strategy_feedback_postprocess_failed",
    )

    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_strategy_decision_review_packet.py",
            "--strategy-trace-json-path",
            str(strategy_trace_json),
            "--strategy-trend-json-path",
            str(strategy_trend_json),
            "--scored-discovery-json-path",
            str(scored_json),
            "--calibration-trend-json-path",
            str(calib_trend_json),
            "--output-json",
            str(review_json),
            "--output-md",
            str(review_md),
            "--context-run-label",
            "v17_supervised_exit_workflow",
        ],
        env=_python_env(),
        error="v17_strategy_review_packet_failed",
    )
    print("v17-supervised-exit-workflow: done")


def preset_v17_m3_quality_checkpoint(args: argparse.Namespace) -> None:
    preset_v16_performance_summary(args)
    preset_v16_go_no_go_gate()
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_scored_candidate_calibration_trend_summary.py",
            "--outcome-log-jsonl-path",
            r".\data\exports\v16_scored_candidate_outcomes.jsonl",
            "--output-json",
            r".\data\exports\v16_scored_candidate_calibration_trend_summary.json",
            "--output-md",
            r".\data\exports\v16_scored_candidate_calibration_trend_summary.md",
            "--group-by",
            "date",
        ],
        env=_python_env(),
        error="v17_m3_calibration_trend_failed",
    )
    print("v17-m3-quality-checkpoint: done")


def preset_v17_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v17_m3_quality_checkpoint(args)
    preset_v17_supervised_exit_workflow(args)
    print("v17-milestone-cycle: done")


def preset_v18_controlled_semi_auto_readiness(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    run_bundles_dir = exports_dir / "run_bundles"
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_daily_live_validation_packet.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v18_daily_live_validation_packet.json"),
            "--output-md",
            str(exports_dir / "v18_daily_live_validation_packet.md"),
            "--pack-dir",
            str(run_bundles_dir),
        ],
        env=_python_env(),
        error="v18_daily_validation_packet_failed",
    )
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_validation_trend_summary.py",
            "--run-bundles-dir",
            str(run_bundles_dir),
            "--max-bundles",
            "50",
            "--output-json",
            str(exports_dir / "v18_validation_trend_summary.json"),
            "--output-md",
            str(exports_dir / "v18_validation_trend_summary.md"),
        ],
        env=_python_env(),
        error="v18_validation_trend_summary_failed",
    )
    preset_v16_performance_summary(args)
    preset_v16_go_no_go_gate()
    print("v18-controlled-semi-auto-readiness: done")


def preset_v19_adaptive_learning_snapshot(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    candidate_json = exports_dir / "v16_discovery_candidates.json"
    outcome_jsonl = exports_dir / "v16_scored_candidate_outcomes.jsonl"
    if not candidate_json.exists():
        raise RuntimeError(f"v19_candidate_json_not_found: {candidate_json}")
    if not outcome_jsonl.exists():
        raise RuntimeError(f"v19_outcome_jsonl_not_found: {outcome_jsonl}")

    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_scored_candidate_calibration_trend_summary.py",
            "--outcome-log-jsonl-path",
            str(outcome_jsonl),
            "--output-json",
            str(exports_dir / "v19_scored_candidate_calibration_trend_by_label.json"),
            "--output-md",
            str(exports_dir / "v19_scored_candidate_calibration_trend_by_label.md"),
            "--group-by",
            "run_label",
        ],
        env=_python_env(),
        error="v19_calibration_trend_by_label_failed",
    )
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_strategy_decision_trace_trend_summary.py",
            "--strategy-trace-log-jsonl-path",
            str(exports_dir / "strategy_decision_traces.jsonl"),
            "--output-json",
            str(exports_dir / "v19_strategy_decision_trend_by_label.json"),
            "--output-md",
            str(exports_dir / "v19_strategy_decision_trend_by_label.md"),
            "--group-by",
            "run_label",
        ],
        env=_python_env(),
        error="v19_strategy_trend_by_label_failed",
    )
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_strategy_decision_trace.py",
            "--candidate-json-path",
            str(candidate_json),
            "--outcome-log-jsonl-path",
            str(outcome_jsonl),
            "--output-json",
            str(exports_dir / "v19_adaptive_strategy_decision_trace.json"),
            "--output-md",
            str(exports_dir / "v19_adaptive_strategy_decision_trace.md"),
            "--entry-adaptive-from-feedback",
            "--exit-policy-adaptive-from-feedback",
        ],
        env=_python_env(),
        error="v19_adaptive_strategy_trace_failed",
    )
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_strategy_decision_review_packet.py",
            "--strategy-trace-json-path",
            str(exports_dir / "v19_adaptive_strategy_decision_trace.json"),
            "--strategy-trend-json-path",
            str(exports_dir / "v19_strategy_decision_trend_by_label.json"),
            "--scored-discovery-json-path",
            str(exports_dir / "v16_scored_discovery_report.json"),
            "--calibration-trend-json-path",
            str(exports_dir / "v19_scored_candidate_calibration_trend_by_label.json"),
            "--output-json",
            str(exports_dir / "v19_adaptive_learning_review_packet.json"),
            "--output-md",
            str(exports_dir / "v19_adaptive_learning_review_packet.md"),
            "--context-run-label",
            "v19_adaptive_learning_snapshot",
        ],
        env=_python_env(),
        error="v19_adaptive_learning_review_packet_failed",
    )
    print("v19-adaptive-learning-snapshot: done")


def preset_v20_m1_monitoring_incident(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v20_monitoring_incident_packet.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v20_monitoring_incident_packet.json"),
            "--output-md",
            str(exports_dir / "v20_monitoring_incident_packet.md"),
        ],
        env=_python_env(),
        error="v20_m1_monitoring_incident_failed",
    )
    print("v20-m1-monitoring-incident: done")


def preset_v20_m2_rollback_scaling(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v20_rollback_scaling_policy.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v20_rollback_scaling_policy.json"),
            "--output-md",
            str(exports_dir / "v20_rollback_scaling_policy.md"),
        ],
        env=_python_env(),
        error="v20_m2_rollback_scaling_failed",
    )
    print("v20-m2-rollback-scaling: done")


def preset_v20_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v20_m1_monitoring_incident(args)
    preset_v20_m2_rollback_scaling(args)
    print("v20-milestone-cycle: done")


def preset_v21_m1_incident_ack_enforcement(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v21_incident_ack_enforcement.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v21_incident_ack_enforcement.json"),
            "--output-md",
            str(exports_dir / "v21_incident_ack_enforcement.md"),
            "--require-ack",
        ],
        env=_python_env(),
        error="v21_m1_incident_ack_enforcement_failed",
    )
    print("v21-m1-incident-ack-enforcement: done")


def preset_v21_m2_automated_rollback_hooks(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v21_automated_rollback_hooks.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v21_automated_rollback_hooks.json"),
            "--output-md",
            str(exports_dir / "v21_automated_rollback_hooks.md"),
        ],
        env=_python_env(),
        error="v21_m2_automated_rollback_hooks_failed",
    )
    print("v21-m2-automated-rollback-hooks: done")


def preset_v21_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v21_m1_incident_ack_enforcement(args)
    preset_v21_m2_automated_rollback_hooks(args)
    print("v21-milestone-cycle: done")


def preset_v22_m1_security_check(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v22_security_check.py",
            "--repo-root",
            str(REPO_ROOT),
            "--exports-dir",
            str(exports_dir),
            "--config-path",
            args.config_path,
            "--output-json",
            str(exports_dir / "v22_security_check.json"),
            "--output-md",
            str(exports_dir / "v22_security_check.md"),
        ],
        env=_python_env(),
        error="v22_m1_security_check_failed",
    )
    print("v22-m1-security-check: done")


def preset_v22_m2_release_criteria_pack(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v22_release_criteria_pack.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v22_release_criteria_pack.json"),
            "--output-md",
            str(exports_dir / "v22_release_criteria_pack.md"),
        ],
        env=_python_env(),
        error="v22_m2_release_criteria_pack_failed",
    )
    print("v22-m2-release-criteria-pack: done")


def preset_v22_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v22_m1_security_check(args)
    preset_v22_m2_release_criteria_pack(args)
    print("v22-milestone-cycle: done")


def preset_v23_m1_operator_ack_completion(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v23_operator_ack_action.py",
            "--exports-dir",
            str(exports_dir),
            "--operator-id",
            "main_user",
            "--action",
            "ack_incident_review_complete",
            "--notes",
            "v23 milestone auto-ack",
            "--output-json",
            str(exports_dir / "v23_operator_ack_action.json"),
        ],
        env=_python_env(),
        error="v23_m1_operator_ack_completion_failed",
    )
    print("v23-m1-operator-ack-completion: done")


def preset_v23_m2_blocker_clearance_recheck(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    # Re-evaluate dependent gates after ack update.
    preset_v21_milestone_cycle(args)
    preset_v22_milestone_cycle(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v23_clearance_summary.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v23_clearance_summary.json"),
            "--output-md",
            str(exports_dir / "v23_clearance_summary.md"),
        ],
        env=_python_env(),
        error="v23_m2_blocker_clearance_recheck_failed",
    )
    print("v23-m2-blocker-clearance-recheck: done")


def preset_v23_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v23_m1_operator_ack_completion(args)
    preset_v23_m2_blocker_clearance_recheck(args)
    print("v23-milestone-cycle: done")


def preset_v24_m1_supervised_scale_trial(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    # Security check always first by default.
    preset_v22_m1_security_check(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v24_supervised_scale_trial.py",
            "--exports-dir",
            str(exports_dir),
            "--config-path",
            str(REPO_ROOT / args.config_path),
            "--output-json",
            str(exports_dir / "v24_supervised_scale_trial.json"),
            "--output-md",
            str(exports_dir / "v24_supervised_scale_trial.md"),
        ],
        env=_python_env(),
        error="v24_m1_supervised_scale_trial_failed",
    )
    print("v24-m1-supervised-scale-trial: done")


def preset_v24_m2_scale_trial_verification(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    # Re-run security and criteria before verification decision.
    preset_v22_milestone_cycle(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v24_scale_trial_verification.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v24_scale_trial_verification.json"),
            "--output-md",
            str(exports_dir / "v24_scale_trial_verification.md"),
        ],
        env=_python_env(),
        error="v24_m2_scale_trial_verification_failed",
    )
    print("v24-m2-scale-trial-verification: done")


def preset_v24_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v24_m1_supervised_scale_trial(args)
    preset_v24_m2_scale_trial_verification(args)
    print("v24-milestone-cycle: done")


def preset_v25_m1_supervised_scale_window_plan(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    # Security and release gates are refreshed before planning.
    preset_v22_milestone_cycle(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v25_supervised_scale_window_plan.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v25_supervised_scale_window_plan.json"),
            "--output-md",
            str(exports_dir / "v25_supervised_scale_window_plan.md"),
        ],
        env=_python_env(),
        error="v25_m1_supervised_scale_window_plan_failed",
    )
    print("v25-m1-supervised-scale-window-plan: done")


def preset_v25_m2_scale_window_outcome_gate(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    # Reconfirm security posture before outcome gate.
    preset_v22_m1_security_check(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v25_scale_window_outcome_gate.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v25_scale_window_outcome_gate.json"),
            "--output-md",
            str(exports_dir / "v25_scale_window_outcome_gate.md"),
        ],
        env=_python_env(),
        error="v25_m2_scale_window_outcome_gate_failed",
    )
    print("v25-m2-scale-window-outcome-gate: done")


def preset_v25_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v25_m1_supervised_scale_window_plan(args)
    preset_v25_m2_scale_window_outcome_gate(args)
    print("v25-milestone-cycle: done")


def preset_v26_m1_supervised_scale_window_execution_packet(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    # Security/release checks are always refreshed before execution packet.
    preset_v22_milestone_cycle(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v26_supervised_scale_window_execution_packet.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v26_supervised_scale_window_execution_packet.json"),
            "--output-md",
            str(exports_dir / "v26_supervised_scale_window_execution_packet.md"),
        ],
        env=_python_env(),
        error="v26_m1_supervised_scale_window_execution_packet_failed",
    )
    print("v26-m1-supervised-scale-window-execution-packet: done")


def preset_v26_m2_supervised_scale_window_closeout(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    # Reconfirm security posture before closeout decision.
    preset_v22_m1_security_check(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v26_supervised_scale_window_closeout.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v26_supervised_scale_window_closeout.json"),
            "--output-md",
            str(exports_dir / "v26_supervised_scale_window_closeout.md"),
        ],
        env=_python_env(),
        error="v26_m2_supervised_scale_window_closeout_failed",
    )
    print("v26-m2-supervised-scale-window-closeout: done")


def preset_v26_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v26_m1_supervised_scale_window_execution_packet(args)
    preset_v26_m2_supervised_scale_window_closeout(args)
    print("v26-milestone-cycle: done")


def preset_v27_m1_supervised_limited_batch_scope(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    preset_v22_milestone_cycle(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v27_supervised_limited_batch_scope.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v27_supervised_limited_batch_scope.json"),
            "--output-md",
            str(exports_dir / "v27_supervised_limited_batch_scope.md"),
        ],
        env=_python_env(),
        error="v27_m1_supervised_limited_batch_scope_failed",
    )
    print("v27-m1-supervised-limited-batch-scope: done")


def preset_v27_m2_guardrail_pack(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    preset_v22_m1_security_check(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v27_guardrail_pack.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v27_guardrail_pack.json"),
            "--output-md",
            str(exports_dir / "v27_guardrail_pack.md"),
        ],
        env=_python_env(),
        error="v27_m2_guardrail_pack_failed",
    )
    print("v27-m2-guardrail-pack: done")


def preset_v27_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v27_m1_supervised_limited_batch_scope(args)
    preset_v27_m2_guardrail_pack(args)
    print("v27-milestone-cycle: done")


def preset_v28_m1_limited_batch_execution_candidate(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    preset_v22_milestone_cycle(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v28_limited_batch_execution_candidate.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v28_limited_batch_execution_candidate.json"),
            "--output-md",
            str(exports_dir / "v28_limited_batch_execution_candidate.md"),
        ],
        env=_python_env(),
        error="v28_m1_limited_batch_execution_candidate_failed",
    )
    print("v28-m1-limited-batch-execution-candidate: done")


def preset_v28_m2_limited_batch_readiness_gate(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    preset_v22_m1_security_check(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v28_limited_batch_readiness_gate.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v28_limited_batch_readiness_gate.json"),
            "--output-md",
            str(exports_dir / "v28_limited_batch_readiness_gate.md"),
        ],
        env=_python_env(),
        error="v28_m2_limited_batch_readiness_gate_failed",
    )
    print("v28-m2-limited-batch-readiness-gate: done")


def preset_v28_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v28_m1_limited_batch_execution_candidate(args)
    preset_v28_m2_limited_batch_readiness_gate(args)
    print("v28-milestone-cycle: done")


def preset_v29_m1_limited_batch_execution_packet(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    preset_v22_milestone_cycle(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v29_limited_batch_execution_packet.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v29_limited_batch_execution_packet.json"),
            "--output-md",
            str(exports_dir / "v29_limited_batch_execution_packet.md"),
        ],
        env=_python_env(),
        error="v29_m1_limited_batch_execution_packet_failed",
    )
    print("v29-m1-limited-batch-execution-packet: done")


def preset_v29_m2_limited_batch_closeout_gate(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    preset_v22_m1_security_check(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v29_limited_batch_closeout_gate.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v29_limited_batch_closeout_gate.json"),
            "--output-md",
            str(exports_dir / "v29_limited_batch_closeout_gate.md"),
        ],
        env=_python_env(),
        error="v29_m2_limited_batch_closeout_gate_failed",
    )
    print("v29-m2-limited-batch-closeout-gate: done")


def preset_v29_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v29_m1_limited_batch_execution_packet(args)
    preset_v29_m2_limited_batch_closeout_gate(args)
    print("v29-milestone-cycle: done")


def preset_v30_m1_scale_readiness_packet(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    preset_v22_milestone_cycle(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v30_scale_readiness_packet.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v30_scale_readiness_packet.json"),
            "--output-md",
            str(exports_dir / "v30_scale_readiness_packet.md"),
        ],
        env=_python_env(),
        error="v30_m1_scale_readiness_packet_failed",
    )
    print("v30-m1-scale-readiness-packet: done")


def preset_v30_m2_scale_promotion_gate(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    preset_v22_m1_security_check(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v30_scale_promotion_gate.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v30_scale_promotion_gate.json"),
            "--output-md",
            str(exports_dir / "v30_scale_promotion_gate.md"),
        ],
        env=_python_env(),
        error="v30_m2_scale_promotion_gate_failed",
    )
    print("v30-m2-scale-promotion-gate: done")


def preset_v30_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v30_m1_scale_readiness_packet(args)
    preset_v30_m2_scale_promotion_gate(args)
    print("v30-milestone-cycle: done")


def preset_v31_m1_supervised_scale_promotion_packet(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    preset_v22_milestone_cycle(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v31_supervised_scale_promotion_packet.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v31_supervised_scale_promotion_packet.json"),
            "--output-md",
            str(exports_dir / "v31_supervised_scale_promotion_packet.md"),
        ],
        env=_python_env(),
        error="v31_m1_supervised_scale_promotion_packet_failed",
    )
    print("v31-m1-supervised-scale-promotion-packet: done")


def preset_v31_m2_supervised_scale_promotion_closeout_gate(args: argparse.Namespace) -> None:
    exports_dir = REPO_ROOT / args.exports_dir
    preset_v22_m1_security_check(args)
    _run_or_raise(
        [
            sys.executable,
            r".\examples\export_v31_supervised_scale_promotion_closeout_gate.py",
            "--exports-dir",
            str(exports_dir),
            "--output-json",
            str(exports_dir / "v31_supervised_scale_promotion_closeout_gate.json"),
            "--output-md",
            str(exports_dir / "v31_supervised_scale_promotion_closeout_gate.md"),
        ],
        env=_python_env(),
        error="v31_m2_supervised_scale_promotion_closeout_gate_failed",
    )
    print("v31-m2-supervised-scale-promotion-closeout-gate: done")


def preset_v31_milestone_cycle(args: argparse.Namespace) -> None:
    preset_v31_m1_supervised_scale_promotion_packet(args)
    preset_v31_m2_supervised_scale_promotion_closeout_gate(args)
    print("v31-milestone-cycle: done")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Python preset runner for live pilot workflows (VS Code-friendly).")
    p.add_argument(
        "--preset",
        default="status_only",
        choices=[
            "status_only",
            "v2_intelligence_report",
            "v2_1_auto_signals",
            "v2_2_deployer_history_store",
            "v2_3_wallet_linkage_engine",
            "v2_4_structural_risk_enhancements",
            "v2_5_execution_bridge",
            "v2_6_calibration_loop",
            "v2_8_acceptance_packet",
            "v2_9_security_preflight",
            "v2_10_closeout_packet",
            "v2_milestone_cycle",
            "v16_supervised_discovery_live",
            "v16_supervised_discovery_postprocess",
            "v16_performance_summary",
            "v16_go_no_go_gate",
            "v16_release_cycle",
            "v17_supervised_exit_workflow",
            "v17_m3_quality_checkpoint",
            "v17_milestone_cycle",
            "v18_controlled_semi_auto_readiness",
            "v19_adaptive_learning_snapshot",
            "v20_m1_monitoring_incident",
            "v20_m2_rollback_scaling",
            "v20_milestone_cycle",
            "v21_m1_incident_ack_enforcement",
            "v21_m2_automated_rollback_hooks",
            "v21_milestone_cycle",
            "v22_m1_security_check",
            "v22_m2_release_criteria_pack",
            "v22_milestone_cycle",
            "v23_m1_operator_ack_completion",
            "v23_m2_blocker_clearance_recheck",
            "v23_milestone_cycle",
            "v24_m1_supervised_scale_trial",
            "v24_m2_scale_trial_verification",
            "v24_milestone_cycle",
            "v25_m1_supervised_scale_window_plan",
            "v25_m2_scale_window_outcome_gate",
            "v25_milestone_cycle",
            "v26_m1_supervised_scale_window_execution_packet",
            "v26_m2_supervised_scale_window_closeout",
            "v26_milestone_cycle",
            "v27_m1_supervised_limited_batch_scope",
            "v27_m2_guardrail_pack",
            "v27_milestone_cycle",
            "v28_m1_limited_batch_execution_candidate",
            "v28_m2_limited_batch_readiness_gate",
            "v28_milestone_cycle",
            "v29_m1_limited_batch_execution_packet",
            "v29_m2_limited_batch_closeout_gate",
            "v29_milestone_cycle",
            "v30_m1_scale_readiness_packet",
            "v30_m2_scale_promotion_gate",
            "v30_milestone_cycle",
            "v31_m1_supervised_scale_promotion_packet",
            "v31_m2_supervised_scale_promotion_closeout_gate",
            "v31_milestone_cycle",
        ],
    )
    p.add_argument("--config-path", default="data/exports/live_pilot_solana_send_pilot_live_enabled_temp.json")
    p.add_argument("--usd-size", type=float, default=0.25)
    p.add_argument("--poll-interval-seconds", type=int, default=1)
    p.add_argument("--dexscreener-fetch-url", default="https://api.dexscreener.com/latest/dex/search/?q=solana%20pump")
    p.add_argument("--dexscreener-fallback-urls-json-path", default="examples/dexscreener_fallback_urls_demo.json")
    p.add_argument("--min-liquidity-usd", type=float, default=25000.0)
    p.add_argument("--max-pair-age-seconds", type=float, default=900.0)
    p.add_argument("--min-volume-5m-usd", type=float, default=6000.0)
    p.add_argument("--max-abs-price-change-5m-pct", type=float, default=18.0)
    p.add_argument("--promote-min-score-total", type=float, default=30.0)
    p.add_argument("--promote-max-candidates", type=int, default=1)
    p.add_argument("--promote-require-probe-ok", action="store_true")
    p.add_argument("--max-fetched-candidates", type=int, default=120)
    p.add_argument(
        "--dexscreener-user-agent",
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    )
    p.add_argument("--skip-status-check", action="store_true")
    p.add_argument("--exports-dir", default="data/exports")
    p.add_argument("--v2-signals-json-path", default="data/exports/v2_intelligence_signals.json")
    p.add_argument("--v2-min-composite-score-to-trade", type=float, default=60.0)
    p.add_argument("--v2-max-executable-candidates", type=int, default=3)
    p.add_argument("--v2-live-gate-json-path", default="data/exports/v2_execution_bridge.json")
    p.add_argument("--skip-v2-live-gate-refresh", action="store_true")
    p.add_argument("--skip-v2-live-gate-enforcement", action="store_true")
    p.add_argument("--skip-v2-security-preflight", action="store_true")
    p.add_argument("--receipt-export-retry-attempts", type=int, default=4)
    p.add_argument("--receipt-export-retry-initial-delay-seconds", type=float, default=2.0)
    p.add_argument("--receipt-export-retry-backoff-seconds", type=float, default=2.0)
    p.add_argument("--v16-bundles-dir", default="data/exports/v16_bundles")
    p.add_argument("--owner-pubkey", default="")
    p.add_argument("--summary-only", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    print(f"preset={args.preset}")
    if args.preset == "status_only":
        preset_status_only()
    elif args.preset == "v2_intelligence_report":
        preset_v2_intelligence_report(args)
    elif args.preset == "v2_1_auto_signals":
        preset_v2_1_auto_signals(args)
    elif args.preset == "v2_2_deployer_history_store":
        preset_v2_2_deployer_history_store(args)
    elif args.preset == "v2_3_wallet_linkage_engine":
        preset_v2_3_wallet_linkage_engine(args)
    elif args.preset == "v2_4_structural_risk_enhancements":
        preset_v2_4_structural_risk_enhancements(args)
    elif args.preset == "v2_5_execution_bridge":
        preset_v2_5_execution_bridge(args)
    elif args.preset == "v2_6_calibration_loop":
        preset_v2_6_calibration_loop(args)
    elif args.preset == "v2_8_acceptance_packet":
        preset_v2_8_acceptance_packet(args)
    elif args.preset == "v2_9_security_preflight":
        preset_v2_9_security_preflight(args)
    elif args.preset == "v2_10_closeout_packet":
        preset_v2_10_closeout_packet(args)
    elif args.preset == "v2_milestone_cycle":
        preset_v2_milestone_cycle(args)
    elif args.preset == "v16_supervised_discovery_live":
        preset_v16_supervised_discovery_live(args)
    elif args.preset == "v16_supervised_discovery_postprocess":
        preset_v16_supervised_discovery_postprocess(args)
    elif args.preset == "v16_performance_summary":
        preset_v16_performance_summary(args)
    elif args.preset == "v16_go_no_go_gate":
        preset_v16_go_no_go_gate()
    elif args.preset == "v16_release_cycle":
        preset_v16_release_cycle(args)
    elif args.preset == "v17_supervised_exit_workflow":
        preset_v17_supervised_exit_workflow(args)
    elif args.preset == "v17_m3_quality_checkpoint":
        preset_v17_m3_quality_checkpoint(args)
    elif args.preset == "v17_milestone_cycle":
        preset_v17_milestone_cycle(args)
    elif args.preset == "v18_controlled_semi_auto_readiness":
        preset_v18_controlled_semi_auto_readiness(args)
    elif args.preset == "v19_adaptive_learning_snapshot":
        preset_v19_adaptive_learning_snapshot(args)
    elif args.preset == "v20_m1_monitoring_incident":
        preset_v20_m1_monitoring_incident(args)
    elif args.preset == "v20_m2_rollback_scaling":
        preset_v20_m2_rollback_scaling(args)
    elif args.preset == "v20_milestone_cycle":
        preset_v20_milestone_cycle(args)
    elif args.preset == "v21_m1_incident_ack_enforcement":
        preset_v21_m1_incident_ack_enforcement(args)
    elif args.preset == "v21_m2_automated_rollback_hooks":
        preset_v21_m2_automated_rollback_hooks(args)
    elif args.preset == "v21_milestone_cycle":
        preset_v21_milestone_cycle(args)
    elif args.preset == "v22_m1_security_check":
        preset_v22_m1_security_check(args)
    elif args.preset == "v22_m2_release_criteria_pack":
        preset_v22_m2_release_criteria_pack(args)
    elif args.preset == "v22_milestone_cycle":
        preset_v22_milestone_cycle(args)
    elif args.preset == "v23_m1_operator_ack_completion":
        preset_v23_m1_operator_ack_completion(args)
    elif args.preset == "v23_m2_blocker_clearance_recheck":
        preset_v23_m2_blocker_clearance_recheck(args)
    elif args.preset == "v23_milestone_cycle":
        preset_v23_milestone_cycle(args)
    elif args.preset == "v24_m1_supervised_scale_trial":
        preset_v24_m1_supervised_scale_trial(args)
    elif args.preset == "v24_m2_scale_trial_verification":
        preset_v24_m2_scale_trial_verification(args)
    elif args.preset == "v24_milestone_cycle":
        preset_v24_milestone_cycle(args)
    elif args.preset == "v25_m1_supervised_scale_window_plan":
        preset_v25_m1_supervised_scale_window_plan(args)
    elif args.preset == "v25_m2_scale_window_outcome_gate":
        preset_v25_m2_scale_window_outcome_gate(args)
    elif args.preset == "v25_milestone_cycle":
        preset_v25_milestone_cycle(args)
    elif args.preset == "v26_m1_supervised_scale_window_execution_packet":
        preset_v26_m1_supervised_scale_window_execution_packet(args)
    elif args.preset == "v26_m2_supervised_scale_window_closeout":
        preset_v26_m2_supervised_scale_window_closeout(args)
    elif args.preset == "v26_milestone_cycle":
        preset_v26_milestone_cycle(args)
    elif args.preset == "v27_m1_supervised_limited_batch_scope":
        preset_v27_m1_supervised_limited_batch_scope(args)
    elif args.preset == "v27_m2_guardrail_pack":
        preset_v27_m2_guardrail_pack(args)
    elif args.preset == "v27_milestone_cycle":
        preset_v27_milestone_cycle(args)
    elif args.preset == "v28_m1_limited_batch_execution_candidate":
        preset_v28_m1_limited_batch_execution_candidate(args)
    elif args.preset == "v28_m2_limited_batch_readiness_gate":
        preset_v28_m2_limited_batch_readiness_gate(args)
    elif args.preset == "v28_milestone_cycle":
        preset_v28_milestone_cycle(args)
    elif args.preset == "v29_m1_limited_batch_execution_packet":
        preset_v29_m1_limited_batch_execution_packet(args)
    elif args.preset == "v29_m2_limited_batch_closeout_gate":
        preset_v29_m2_limited_batch_closeout_gate(args)
    elif args.preset == "v29_milestone_cycle":
        preset_v29_milestone_cycle(args)
    elif args.preset == "v30_m1_scale_readiness_packet":
        preset_v30_m1_scale_readiness_packet(args)
    elif args.preset == "v30_m2_scale_promotion_gate":
        preset_v30_m2_scale_promotion_gate(args)
    elif args.preset == "v30_milestone_cycle":
        preset_v30_milestone_cycle(args)
    elif args.preset == "v31_m1_supervised_scale_promotion_packet":
        preset_v31_m1_supervised_scale_promotion_packet(args)
    elif args.preset == "v31_m2_supervised_scale_promotion_closeout_gate":
        preset_v31_m2_supervised_scale_promotion_closeout_gate(args)
    elif args.preset == "v31_milestone_cycle":
        preset_v31_milestone_cycle(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
