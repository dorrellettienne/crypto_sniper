#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _latest_auto_window_log(exports_dir: Path) -> Path | None:
    rows = sorted(exports_dir.glob("live_pilot_service_auto_window_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return rows[0] if rows else None


def _last_completed_rollup_from_jsonl(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    rollup: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except Exception:
            continue
        if str(row.get("event_type") or "") != "live_pilot_service_completed":
            continue
        payload = row.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("rollup"), dict):
            rollup = dict(payload.get("rollup") or {})
    return rollup


def _add_check(checks: list[dict[str, Any]], name: str, ok: bool, actual: Any, expected: Any) -> None:
    checks.append({"name": name, "ok": bool(ok), "actual": actual, "expected": expected})


def build_readiness(exports_dir: Path) -> dict[str, Any]:
    run_summary = _read_json(exports_dir / "v16_autonomous_run_summary.json")
    gate = _read_json(exports_dir / "v16_go_no_go_gate.json")
    security = _read_json(exports_dir / "v2_security_preflight.json")
    closeout = _read_json(exports_dir / "v2_closeout_packet.json")
    cfg = _read_json(exports_dir / "live_pilot_solana_send_pilot_live_enabled_temp.json")

    auto_log = _latest_auto_window_log(exports_dir)
    rollup = _last_completed_rollup_from_jsonl(auto_log)
    auto = dict(rollup.get("auto_window") or {})
    submit_reasons = dict(rollup.get("submit_dispatch_by_reason") or {})
    sell_reasons = dict(rollup.get("sell_submit_dispatch_by_reason") or {})

    checks: list[dict[str, Any]] = []
    _add_check(
        checks,
        "autonomous_summary_success",
        str(((run_summary.get("summary") or {}).get("status")) or "") == "success",
        str(((run_summary.get("summary") or {}).get("status")) or ""),
        "success",
    )
    _add_check(
        checks,
        "gates_all_true",
        bool(((run_summary.get("gates") or {}).get("v16_go_no_go")))
        and bool(((run_summary.get("gates") or {}).get("v2_security_preflight")))
        and bool(((run_summary.get("gates") or {}).get("v2_closeout"))),
        dict(run_summary.get("gates") or {}),
        {"v16_go_no_go": True, "v2_security_preflight": True, "v2_closeout": True},
    )
    _add_check(
        checks,
        "artifact_gate_ok",
        bool(gate.get("go")),
        bool(gate.get("go")),
        True,
    )
    _add_check(
        checks,
        "artifact_security_ok",
        bool(((security.get("summary") or {}).get("preflight_ok"))),
        bool(((security.get("summary") or {}).get("preflight_ok"))),
        True,
    )
    _add_check(
        checks,
        "artifact_closeout_ok",
        bool(((closeout.get("summary") or {}).get("enable_v2_default_live_gate"))),
        bool(((closeout.get("summary") or {}).get("enable_v2_default_live_gate"))),
        True,
    )
    _add_check(checks, "auto_window_log_present", auto_log is not None, str(auto_log) if auto_log else "", "present")

    trades_submitted = int(auto.get("trades_submitted", 0) or 0)
    sells_submitted = int(auto.get("sells_submitted", 0) or 0)
    _add_check(checks, "at_least_one_trade_submitted", trades_submitted >= 1, trades_submitted, ">= 1")

    auto_exit_enabled = bool(cfg.get("live_auto_exit_enabled", False))
    if auto_exit_enabled:
        _add_check(
            checks,
            "auto_exit_mode_enabled",
            str(cfg.get("manual_submit_mode", "")).strip().lower() in {"buy_and_sell", "buy_sell", "all"},
            str(cfg.get("manual_submit_mode", "")),
            "buy_and_sell",
        )
        _add_check(
            checks,
            "sell_submit_coverage",
            sells_submitted >= trades_submitted and int(rollup.get("sell_submitted_signatures", 0) or 0) >= trades_submitted,
            {
                "trades_submitted": trades_submitted,
                "sells_submitted": sells_submitted,
                "sell_submitted_signatures": int(rollup.get("sell_submitted_signatures", 0) or 0),
            },
            "sell submits/signatures cover all submitted buys",
        )
        non_success_sell_reasons = [k for k, v in sell_reasons.items() if int(v or 0) > 0 and k != "send_raw_transaction_submitted"]
        _add_check(
            checks,
            "no_sell_submit_block_reasons",
            len(non_success_sell_reasons) == 0,
            non_success_sell_reasons,
            [],
        )

    summary_reason = str(((run_summary.get("summary") or {}).get("reason")) or "")
    _add_check(
        checks,
        "no_auto_exit_safety_failure",
        "auto_exit_safety_failed" not in summary_reason,
        summary_reason,
        "does_not_contain:auto_exit_safety_failed",
    )

    ready = all(bool(c.get("ok")) for c in checks)
    failed = [str(c.get("name") or "") for c in checks if not bool(c.get("ok"))]

    return {
        "ok": True,
        "report_version": "final_stage_readiness_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "ready_for_final_stage": ready,
            "failed_checks": failed,
            "auto_exit_enabled": auto_exit_enabled,
            "trades_submitted": trades_submitted,
            "sells_submitted": sells_submitted,
            "latest_auto_window_log": str(auto_log) if auto_log else "",
        },
        "checks": checks,
        "details": {
            "submit_dispatch_by_reason": submit_reasons,
            "sell_submit_dispatch_by_reason": sell_reasons,
            "config_snapshot": {
                "manual_submit_mode": cfg.get("manual_submit_mode"),
                "live_auto_exit_enabled": cfg.get("live_auto_exit_enabled"),
                "live_auto_exit_price_multiplier": cfg.get("live_auto_exit_price_multiplier"),
                "live_send_max_orders_per_session": cfg.get("live_send_max_orders_per_session"),
            },
        },
    }


def _to_md(payload: dict[str, Any]) -> str:
    s = dict(payload.get("summary") or {})
    lines = [
        "# Final Stage Readiness",
        "",
        f"- ready_for_final_stage: `{s.get('ready_for_final_stage')}`",
        f"- failed_checks: `{json.dumps(s.get('failed_checks') or [], separators=(',', ':'))}`",
        f"- auto_exit_enabled: `{s.get('auto_exit_enabled')}`",
        f"- trades_submitted: `{s.get('trades_submitted')}`",
        f"- sells_submitted: `{s.get('sells_submitted')}`",
        f"- latest_auto_window_log: `{s.get('latest_auto_window_log')}`",
        "",
        "## Checks",
    ]
    for row in list(payload.get("checks") or []):
        lines.append(
            f"- {row.get('name')}: ok=`{row.get('ok')}` actual=`{row.get('actual')}` expected=`{row.get('expected')}`"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Export final stage readiness pass/fail packet.")
    p.add_argument("--exports-dir", default="data/exports")
    p.add_argument("--output-json", default="data/exports/final_stage_readiness.json")
    p.add_argument("--output-md", default="data/exports/final_stage_readiness.md")
    p.add_argument("--fail-on-not-ready", action="store_true")
    args = p.parse_args()

    exports_dir = Path(args.exports_dir)
    payload = build_readiness(exports_dir)
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    out_md.write_text(_to_md(payload), encoding="utf-8")

    cli = {
        "ok": True,
        "output_json": str(out_json),
        "output_md": str(out_md),
        "ready_for_final_stage": bool(((payload.get("summary") or {}).get("ready_for_final_stage"))),
        "failed_checks": list((payload.get("summary") or {}).get("failed_checks") or []),
    }
    print(json.dumps(cli, separators=(",", ":")))
    if args.fail_on_not_ready and not cli["ready_for_final_stage"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
