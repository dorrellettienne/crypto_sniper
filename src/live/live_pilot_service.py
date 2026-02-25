import argparse
import json
import time
from pathlib import Path
from typing import Any

from src.live.audit_logger import append_audit_event, build_audit_log_path
from src.live.confirmation_reconciliation import reconcile_live_chain_confirmation
from src.live.dexscreener_transport import DexScreenerHttpPairsFetcher
from src.live.live_execution_adapter import LiveExecutionAdapter
from src.live.interfaces import SignalProvider, TradeSignal
from src.live.mechanical_safety_filter import MechanicalSafetyFilter
from src.live.path_security import ensure_dir_within_base
from src.live.signal_provider_dexscreener import DexScreenerSignalProvider
from src.live.volatility_guard import VolatilityGuard


def _to_float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _to_int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _extract_live_submit_economics(payload: dict[str, Any], *, thresholds: dict[str, Any] | None = None) -> dict[str, Any]:
    thresholds = dict(thresholds or {})
    md = payload.get("metadata") if isinstance(payload, dict) else {}
    if not isinstance(md, dict):
        return {}
    dispatch = md.get("submit_dispatch")
    if not isinstance(dispatch, dict):
        return {}
    chain = dispatch.get("chain_reconciliation")
    if not isinstance(chain, dict):
        return {}
    settlement = chain.get("settlement_summary")
    if not isinstance(settlement, dict):
        return {}

    token_address = str(md.get("token_address") or "").strip()
    token_deltas = settlement.get("token_deltas_by_mint")
    token_deltas = token_deltas if isinstance(token_deltas, dict) else {}
    actual_token_out_raw = None
    if token_address:
        actual_token_out_raw = _to_int_or_none(token_deltas.get(token_address))
    if actual_token_out_raw is None:
        positive_deltas = []
        for v in token_deltas.values():
            iv = _to_int_or_none(v)
            if iv is not None and iv > 0:
                positive_deltas.append(iv)
        if len(positive_deltas) == 1:
            actual_token_out_raw = positive_deltas[0]

    estimated_costs = md.get("estimated_costs") if isinstance(md.get("estimated_costs"), dict) else {}
    order_preview = md.get("order_preview") if isinstance(md.get("order_preview"), dict) else {}
    quote_preview = order_preview.get("quote_preview") if isinstance(order_preview.get("quote_preview"), dict) else {}
    raw_quote = quote_preview.get("raw_quote") if isinstance(quote_preview.get("raw_quote"), dict) else {}

    expected_out_raw = (
        _to_int_or_none((estimated_costs or {}).get("out_amount"))
        if isinstance(estimated_costs, dict)
        else None
    )
    if expected_out_raw is None:
        expected_out_raw = _to_int_or_none(raw_quote.get("outAmount"))
    if expected_out_raw is None:
        expected_out_raw = _to_int_or_none(quote_preview.get("out_amount"))

    out_diff_raw = None
    out_diff_bps_signed = None
    realized_slippage_bps_vs_quote = None
    if expected_out_raw is not None and expected_out_raw > 0 and actual_token_out_raw is not None:
        out_diff_raw = int(actual_token_out_raw) - int(expected_out_raw)
        out_diff_bps_signed = round((float(out_diff_raw) / float(expected_out_raw)) * 10_000.0, 6)
        realized_slippage_bps_vs_quote = round(max(0.0, (float(expected_out_raw) - float(actual_token_out_raw)) / float(expected_out_raw) * 10_000.0), 6)

    mismatch_threshold_bps = _to_float_or_none(thresholds.get("max_quote_to_settlement_diff_bps"))
    if mismatch_threshold_bps is None:
        mismatch_threshold_bps = 0.0
    quote_vs_settlement_mismatch = bool(
        out_diff_bps_signed is not None and abs(float(out_diff_bps_signed)) > float(mismatch_threshold_bps)
    )

    fee_lamports = _to_int_or_none(settlement.get("fee_lamports"))
    fee_usd_realized_proxy = None
    usd_to_lamports = _to_float_or_none((thresholds or {}).get("usd_to_lamports"))
    if fee_lamports is not None and usd_to_lamports and usd_to_lamports > 0:
        fee_usd_realized_proxy = round(float(fee_lamports) / float(usd_to_lamports), 8)

    return {
        "available": True,
        "token_address": token_address or None,
        "symbol": md.get("symbol"),
        "submitted_signature": dispatch.get("submitted_signature"),
        "chain_outcome_class": chain.get("outcome_class"),
        "fee_lamports": fee_lamports,
        "fee_usd_realized_proxy": fee_usd_realized_proxy,
        "estimated_network_fee_usd": _to_float_or_none((estimated_costs or {}).get("network_fee_usd")) if isinstance(estimated_costs, dict) else None,
        "estimated_total_cost_usd": _to_float_or_none((estimated_costs or {}).get("estimated_total_cost_usd")) if isinstance(estimated_costs, dict) else None,
        "estimated_slippage_usd": _to_float_or_none((estimated_costs or {}).get("estimated_slippage_usd")) if isinstance(estimated_costs, dict) else None,
        "estimated_slippage_bps_effective": _to_float_or_none((estimated_costs or {}).get("slippage_bps_effective")) if isinstance(estimated_costs, dict) else None,
        "estimated_notional_usd": _to_float_or_none((estimated_costs or {}).get("notional_usd")) if isinstance(estimated_costs, dict) else _to_float_or_none(md.get("usd_size")),
        "quote_expected_out_amount_raw": expected_out_raw,
        "settlement_actual_out_amount_raw": actual_token_out_raw,
        "quote_vs_settlement_out_diff_raw": out_diff_raw,
        "quote_vs_settlement_out_diff_bps_signed": out_diff_bps_signed,
        "realized_slippage_bps_vs_quote": realized_slippage_bps_vs_quote,
        "quote_vs_settlement_mismatch": quote_vs_settlement_mismatch,
        "quote_vs_settlement_mismatch_threshold_bps": mismatch_threshold_bps,
    }


def _maybe_attach_live_submit_economics(payload: dict[str, Any], cfg: dict[str, Any]) -> None:
    md = payload.get("metadata")
    if not isinstance(md, dict):
        return
    dispatch = md.get("submit_dispatch")
    if not isinstance(dispatch, dict):
        return
    if isinstance(dispatch.get("economics"), dict):
        return
    econ = _extract_live_submit_economics(payload, thresholds=cfg.get("live_pilot_economics_thresholds"))
    if econ:
        dispatch["economics"] = dict(econ)
        md["live_submit_economics"] = dict(econ)


def _rollup_update_from_economics(rollup: dict[str, Any], economics: dict[str, Any]) -> None:
    if not isinstance(economics, dict) or not economics:
        return
    rollup["economics_samples_count"] = int(rollup.get("economics_samples_count", 0)) + 1
    fee_lamports = _to_int_or_none(economics.get("fee_lamports"))
    if fee_lamports is not None:
        rollup["fee_lamports_total"] = int(rollup.get("fee_lamports_total", 0)) + int(fee_lamports)
    if bool(economics.get("quote_vs_settlement_mismatch", False)):
        rollup["quote_vs_settlement_mismatch_count"] = int(rollup.get("quote_vs_settlement_mismatch_count", 0)) + 1
    realized_slippage_bps = _to_float_or_none(economics.get("realized_slippage_bps_vs_quote"))
    if realized_slippage_bps is not None:
        rollup["_realized_slippage_bps_sum"] = round(float(rollup.get("_realized_slippage_bps_sum", 0.0)) + realized_slippage_bps, 6)
        rollup["_realized_slippage_bps_samples"] = int(rollup.get("_realized_slippage_bps_samples", 0)) + 1
        samples = int(rollup.get("_realized_slippage_bps_samples", 0))
        if samples > 0:
            rollup["avg_realized_slippage_bps"] = round(float(rollup.get("_realized_slippage_bps_sum", 0.0)) / float(samples), 6)
        current_worst = _to_float_or_none(rollup.get("worst_realized_slippage_bps"))
        if current_worst is None or realized_slippage_bps > current_worst:
            rollup["worst_realized_slippage_bps"] = round(realized_slippage_bps, 6)


def _rollup_update_from_payload(rollup: dict[str, Any], payload: dict[str, Any]) -> None:
    md = payload.get("metadata") if isinstance(payload, dict) else {}
    dispatch = md.get("submit_dispatch") if isinstance(md, dict) else {}
    if isinstance(dispatch, dict):
        _rollup_update_from_dispatch(rollup, dispatch)
        if isinstance(dispatch.get("economics"), dict):
            _rollup_update_from_economics(rollup, dispatch.get("economics"))


def _evaluate_live_pilot_promotion_gates(rollup: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    rollup = dict(rollup or {})
    cfg = dict(cfg or {})
    thresholds = {
        "min_finalized_pilots": int(cfg.get("min_finalized_pilots", 3)),
        "max_reconciliation_mismatches": int(cfg.get("max_reconciliation_mismatches", 0)),
        "max_pause_latch_events": int(cfg.get("max_pause_latch_events", 0)),
        "max_quote_vs_settlement_mismatches": int(cfg.get("max_quote_vs_settlement_mismatches", 0)),
        "max_worst_realized_slippage_bps": float(cfg.get("max_worst_realized_slippage_bps", 150.0)),
        "max_dexscreener_transport_errors": int(cfg.get("max_dexscreener_transport_errors", 0)),
        "max_dexscreener_endpoint_failures": int(cfg.get("max_dexscreener_endpoint_failures", 0)),
        "min_economics_samples": int(cfg.get("min_economics_samples", 1)),
    }
    provider_metrics = dict(rollup.get("signal_provider_metrics") or {})
    metrics = {
        "live_finalized_count": int(rollup.get("live_finalized_count", 0)),
        "live_reconciliation_mismatch_count": int(rollup.get("live_reconciliation_mismatch_count", 0)),
        "pause_latch_events": int(rollup.get("pause_latch_events", 0)),
        "quote_vs_settlement_mismatch_count": int(rollup.get("quote_vs_settlement_mismatch_count", 0)),
        "worst_realized_slippage_bps": _to_float_or_none(rollup.get("worst_realized_slippage_bps")),
        "economics_samples_count": int(rollup.get("economics_samples_count", 0)),
        "dexscreener_transport_errors": int(provider_metrics.get("fetch_transport_errors", 0)),
        "dexscreener_endpoint_failures": int(provider_metrics.get("fetch_endpoint_failure_events", 0)),
    }

    checks = [
        ("min_finalized_pilots", metrics["live_finalized_count"] >= thresholds["min_finalized_pilots"], metrics["live_finalized_count"], thresholds["min_finalized_pilots"], ">="),
        (
            "max_reconciliation_mismatches",
            metrics["live_reconciliation_mismatch_count"] <= thresholds["max_reconciliation_mismatches"],
            metrics["live_reconciliation_mismatch_count"],
            thresholds["max_reconciliation_mismatches"],
            "<=",
        ),
        ("max_pause_latch_events", metrics["pause_latch_events"] <= thresholds["max_pause_latch_events"], metrics["pause_latch_events"], thresholds["max_pause_latch_events"], "<="),
        (
            "max_quote_vs_settlement_mismatches",
            metrics["quote_vs_settlement_mismatch_count"] <= thresholds["max_quote_vs_settlement_mismatches"],
            metrics["quote_vs_settlement_mismatch_count"],
            thresholds["max_quote_vs_settlement_mismatches"],
            "<=",
        ),
        (
            "max_worst_realized_slippage_bps",
            (metrics["worst_realized_slippage_bps"] if metrics["worst_realized_slippage_bps"] is not None else float("inf"))
            <= thresholds["max_worst_realized_slippage_bps"],
            metrics["worst_realized_slippage_bps"],
            thresholds["max_worst_realized_slippage_bps"],
            "<=",
        ),
        (
            "max_dexscreener_transport_errors",
            metrics["dexscreener_transport_errors"] <= thresholds["max_dexscreener_transport_errors"],
            metrics["dexscreener_transport_errors"],
            thresholds["max_dexscreener_transport_errors"],
            "<=",
        ),
        (
            "max_dexscreener_endpoint_failures",
            metrics["dexscreener_endpoint_failures"] <= thresholds["max_dexscreener_endpoint_failures"],
            metrics["dexscreener_endpoint_failures"],
            thresholds["max_dexscreener_endpoint_failures"],
            "<=",
        ),
        (
            "min_economics_samples",
            metrics["economics_samples_count"] >= thresholds["min_economics_samples"],
            metrics["economics_samples_count"],
            thresholds["min_economics_samples"],
            ">=",
        ),
    ]
    check_rows = [
        {"name": name, "ok": bool(ok), "actual": actual, "threshold": threshold, "op": op}
        for name, ok, actual, threshold, op in checks
    ]
    failed_checks = [row["name"] for row in check_rows if not bool(row.get("ok", False))]
    ready = len(failed_checks) == 0
    return {
        "status": "pass" if ready else "fail",
        "ready_to_promote": ready,
        "failed_checks": failed_checks,
        "checks": check_rows,
        "thresholds": thresholds,
        "metrics": metrics,
        "summary": ("promotion_gate_pass" if ready else f"promotion_gate_fail:{','.join(failed_checks)}"),
    }


def _merge_count_map(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    out = dict(dst or {})
    for k, v in dict(src or {}).items():
        out[str(k)] = int(out.get(str(k), 0)) + int(v or 0)
    return out


def _summarize_campaign_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"count": 0, "by_level": {}, "by_type": {}, "last_alert": {}}
    for row in list(alerts or []):
        if not isinstance(row, dict):
            continue
        out["count"] = int(out.get("count", 0)) + 1
        level = str(row.get("level") or "")
        if level:
            by_level = out.setdefault("by_level", {})
            by_level[level] = int(by_level.get(level, 0)) + 1
        atype = str(row.get("alert_type") or "")
        if atype:
            by_type = out.setdefault("by_type", {})
            by_type[atype] = int(by_type.get(atype, 0)) + 1
        out["last_alert"] = {
            "alert_type": atype,
            "level": level,
            "message": str(row.get("message") or ""),
            "run_index": row.get("run_index"),
        }
    return out


def _build_campaign_alert_emitter(
    *,
    campaign_id: str,
    alerts_jsonl_path: str = "",
    console: bool = False,
    webhook_url: str = "",
):
    alerts_file = Path(alerts_jsonl_path) if str(alerts_jsonl_path or "").strip() else None

    def emit(alert: dict[str, Any]) -> None:
        row = {
            "ts_unix_ms": int(time.time() * 1000),
            "event_type": "live_pilot_campaign_alert",
            "campaign_id": campaign_id,
            **dict(alert or {}),
        }
        if webhook_url:
            # Future-safe placeholder: surface config presence without performing network calls.
            row.setdefault("webhook_configured", True)
        if alerts_file:
            with alerts_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        if console:
            print(
                f"[campaign-alert] {row.get('level','info')} {row.get('alert_type','')}: {row.get('message','')}",
                flush=True,
            )

    return emit


def _campaign_alert_policy_eval(
    *,
    run_out: dict[str, Any] | None,
    aggregate_rollup: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    policy = dict(policy or {})
    run_rollup = dict((run_out or {}).get("rollup") or {})
    agg = dict(aggregate_rollup or {})
    out: list[dict[str, Any]] = []

    if int(run_rollup.get("pause_latch_events", 0)) > 0:
        out.append({"alert_type": "pause_latch_triggered", "level": "critical", "message": "Pause latch triggered during campaign run."})
    if int(run_rollup.get("live_reconciliation_mismatch_count", 0)) > 0:
        out.append({"alert_type": "reconciliation_mismatch", "level": "critical", "message": "Reconciliation mismatch detected during campaign run."})

    rpm = dict(run_rollup.get("signal_provider_metrics") or {})
    transport_errors = int(rpm.get("fetch_transport_errors", 0) or 0)
    endpoint_failures = int(rpm.get("fetch_endpoint_failure_events", 0) or 0)
    max_run_transport_errors = int(policy.get("max_run_transport_errors_before_critical", 0))
    max_run_endpoint_failures = int(policy.get("max_run_endpoint_failures_before_critical", 0))
    if transport_errors > 0:
        out.append(
            {
                "alert_type": "dexscreener_transport_errors",
                "level": ("critical" if transport_errors > max_run_transport_errors else "warning"),
                "message": f"DexScreener transport errors observed: {transport_errors}",
                "details": {"fetch_transport_errors": transport_errors, "last_error": str(rpm.get('last_error') or "")},
            }
        )
    if endpoint_failures > 0:
        out.append(
            {
                "alert_type": "dexscreener_endpoint_failures",
                "level": ("critical" if endpoint_failures > max_run_endpoint_failures else "warning"),
                "message": f"DexScreener endpoint failures observed: {endpoint_failures}",
                "details": {"fetch_endpoint_failure_events": endpoint_failures},
            }
        )

    apm = dict(agg.get("signal_provider_metrics") or {})
    agg_transport = int(apm.get("fetch_transport_errors", 0) or 0)
    agg_endpoints = int(apm.get("fetch_endpoint_failure_events", 0) or 0)
    max_campaign_transport_errors = policy.get("max_campaign_transport_errors")
    max_campaign_endpoint_failures = policy.get("max_campaign_endpoint_failures")
    if max_campaign_transport_errors not in (None, "") and agg_transport > int(max_campaign_transport_errors):
        out.append(
            {
                "alert_type": "campaign_dexscreener_transport_error_threshold_exceeded",
                "level": "critical",
                "message": f"Campaign DexScreener transport errors exceeded threshold ({agg_transport} > {int(max_campaign_transport_errors)}).",
                "details": {"aggregate_fetch_transport_errors": agg_transport},
            }
        )
    if max_campaign_endpoint_failures not in (None, "") and agg_endpoints > int(max_campaign_endpoint_failures):
        out.append(
            {
                "alert_type": "campaign_dexscreener_endpoint_failure_threshold_exceeded",
                "level": "critical",
                "message": f"Campaign DexScreener endpoint failures exceeded threshold ({agg_endpoints} > {int(max_campaign_endpoint_failures)}).",
                "details": {"aggregate_fetch_endpoint_failure_events": agg_endpoints},
            }
        )
    return out


def _accumulate_campaign_rollup(campaign_rollup: dict[str, Any], run_rollup: dict[str, Any]) -> None:
    rr = dict(run_rollup or {})
    campaign_rollup["campaign_runs_completed"] = int(campaign_rollup.get("campaign_runs_completed", 0)) + 1
    for key in (
        "runs",
        "submitted_signatures",
        "pause_latch_events",
        "pause_reset_events",
        "live_finalized_count",
        "live_reconciliation_mismatch_count",
        "economics_samples_count",
        "fee_lamports_total",
        "quote_vs_settlement_mismatch_count",
        "signals_seen",
        "signals_accepted",
        "signals_rejected",
        "candidates_seen",
        "candidates_attempted",
        "candidates_submitted",
    ):
        if rr.get(key) is not None:
            campaign_rollup[key] = int(campaign_rollup.get(key, 0)) + int(rr.get(key, 0) or 0)

    for key in (
        "submit_dispatch_by_reason",
        "live_reconciliation_outcome_by_class",
        "signal_rejected_by_reason",
        "candidate_submit_reason_by_reason",
        "candidate_skip_reason_by_reason",
        "mechanical_blocked_by_reason",
        "volatility_guard_blocked_by_reason",
    ):
        if isinstance(rr.get(key), dict):
            campaign_rollup[key] = _merge_count_map(campaign_rollup.get(key, {}), rr.get(key, {}))

    pm = dict(rr.get("signal_provider_metrics") or {})
    if pm:
        cpm = dict(campaign_rollup.get("signal_provider_metrics") or {})
        for key in (
            "fetch_retry_events",
            "fetch_stale_payload_events",
            "fetch_transport_errors",
            "fetch_fallback_selected_events",
            "fetch_endpoint_failure_events",
        ):
            cpm[key] = int(cpm.get(key, 0)) + int(pm.get(key, 0) or 0)
        if pm.get("last_error") is not None:
            cpm["last_error"] = str(pm.get("last_error") or "")
        if isinstance(pm.get("last_fetch_meta"), dict):
            cpm["last_fetch_meta"] = dict(pm.get("last_fetch_meta") or {})
        if isinstance(pm.get("last_payload_stats"), dict):
            cpm["last_payload_stats"] = dict(pm.get("last_payload_stats") or {})
        campaign_rollup["signal_provider_metrics"] = cpm

    for key in ("avg_realized_slippage_bps", "worst_realized_slippage_bps"):
        if key in rr and rr.get(key) is not None:
            current = _to_float_or_none(campaign_rollup.get(key))
            incoming = _to_float_or_none(rr.get(key))
            if incoming is None:
                continue
            if key == "worst_realized_slippage_bps":
                campaign_rollup[key] = incoming if current is None else max(current, incoming)
            else:
                # Weighted average by economics samples when available.
                prev_samples = int(campaign_rollup.get("_campaign_econ_avg_samples", 0))
                prev_avg = _to_float_or_none(campaign_rollup.get("avg_realized_slippage_bps")) or 0.0
                new_samples = int(rr.get("economics_samples_count", 0) or 0)
                if new_samples <= 0:
                    continue
                total_samples = prev_samples + new_samples
                weighted = ((prev_avg * prev_samples) + (incoming * new_samples)) / float(total_samples)
                campaign_rollup["avg_realized_slippage_bps"] = round(weighted, 6)
                campaign_rollup["_campaign_econ_avg_samples"] = total_samples

    auto = dict(rr.get("auto_window") or {})
    if auto:
        cauto = dict(campaign_rollup.get("auto_window") or {})
        cauto["trades_submitted"] = int(cauto.get("trades_submitted", 0)) + int(auto.get("trades_submitted", 0) or 0)
        cauto["cycles_completed"] = int(cauto.get("cycles_completed", 0)) + int(auto.get("cycles_completed", 0) or 0)
        stops = dict(cauto.get("stop_reason_by_reason") or {})
        sr = str(auto.get("stop_reason") or "")
        if sr:
            stops[sr] = int(stops.get(sr, 0)) + 1
        cauto["stop_reason_by_reason"] = stops
        campaign_rollup["auto_window"] = cauto


def _cleanup_campaign_rollup_for_output(rollup: dict[str, Any]) -> dict[str, Any]:
    out = dict(rollup or {})
    out.pop("_campaign_econ_avg_samples", None)
    return out


def _default_campaign_stop_evaluator(run_out: dict[str, Any]) -> dict[str, Any]:
    rollup = dict((run_out or {}).get("rollup") or {})
    if int(rollup.get("pause_latch_events", 0)) > 0:
        return {"stop": True, "reason": "pause_latch_triggered"}
    if int(rollup.get("live_reconciliation_mismatch_count", 0)) > 0:
        return {"stop": True, "reason": "reconciliation_mismatch"}
    if int(((rollup.get("signal_provider_metrics") or {}).get("fetch_transport_errors", 0) or 0)) > 0:
        return {"stop": True, "reason": "signal_provider_transport_error"}
    sdr = dict(rollup.get("submit_dispatch_by_reason") or {})
    for reason in ("send_raw_transaction_error", "submit_signer_error", "signed_submit_stub_error"):
        if int(sdr.get(reason, 0)) > 0:
            return {"stop": True, "reason": reason}
    return {"stop": False, "reason": ""}


def _render_campaign_report_markdown(report: dict[str, Any]) -> str:
    summary = dict(report.get("campaign_summary") or {})
    agg = dict(summary.get("aggregate_rollup") or {})
    gate = dict(summary.get("promotion_gate_summary") or {})
    alerts = dict(summary.get("alert_summary") or {})
    lines = [
        "# Live Pilot Campaign Report",
        "",
        f"- campaign_id: `{summary.get('campaign_id')}`",
        f"- target_runs: `{summary.get('target_runs')}`",
        f"- completed_runs: `{summary.get('completed_runs')}`",
        f"- stop_reason: `{summary.get('stop_reason') or '-'}`",
        "",
        "## Aggregate",
        "",
        f"- live_finalized_count: `{agg.get('live_finalized_count', 0)}`",
        f"- live_reconciliation_mismatch_count: `{agg.get('live_reconciliation_mismatch_count', 0)}`",
        f"- economics_samples_count: `{agg.get('economics_samples_count', 0)}`",
        f"- fee_lamports_total: `{agg.get('fee_lamports_total', 0)}`",
        f"- avg_realized_slippage_bps: `{agg.get('avg_realized_slippage_bps')}`",
        f"- worst_realized_slippage_bps: `{agg.get('worst_realized_slippage_bps')}`",
        "",
        "## Promotion Gate",
        "",
        f"- status: `{gate.get('status')}`",
        f"- ready_to_promote: `{bool(gate.get('ready_to_promote', False))}`",
        f"- failed_checks: `{', '.join(list(gate.get('failed_checks', []) or [])) or '-'}`",
    ]
    if alerts:
        lines.extend(
            [
                "",
                "## Alerts",
                "",
                f"- count: `{alerts.get('count', 0)}`",
                f"- by_level: `{json.dumps(alerts.get('by_level', {}), sort_keys=True)}`",
                f"- by_type: `{json.dumps(alerts.get('by_type', {}), sort_keys=True)}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _write_campaign_report(report: dict[str, Any], report_path: str) -> None:
    path = Path(report_path)
    if path.suffix.lower() in {".md", ".markdown"}:
        path.write_text(_render_campaign_report_markdown(report), encoding="utf-8")
    else:
        path.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def run_live_pilot_campaign(
    *,
    campaign_runs: int,
    run_once_fn,
    campaign_id: str | None = None,
    campaign_state_json_path: str = "",
    campaign_report_path: str = "",
    resume_campaign: bool = False,
    promotion_gate_config: dict[str, Any] | None = None,
    stop_evaluator=None,
    alert_emitter=None,
    alert_policy: dict[str, Any] | None = None,
    alert_on_promotion_gate_fail: bool = False,
    alert_on_campaign_stop: bool = False,
) -> dict[str, Any]:
    campaign_runs = int(campaign_runs)
    if campaign_runs <= 0:
        raise ValueError("campaign_runs must be > 0")
    stop_evaluator = stop_evaluator or _default_campaign_stop_evaluator
    campaign_id = str(campaign_id or f"pilot_campaign_{int(time.time())}")
    alert_policy = dict(alert_policy or {})
    alerts_emitted: list[dict[str, Any]] = []

    state_path = Path(campaign_state_json_path) if str(campaign_state_json_path or "").strip() else None
    state: dict[str, Any] = {"campaign_id": campaign_id, "target_runs": campaign_runs, "runs": [], "stop_reason": ""}
    if state_path and state_path.exists():
        if not bool(resume_campaign):
            raise ValueError("campaign state file already exists; pass --resume-campaign to continue")
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("invalid campaign state file")
        loaded_id = str(loaded.get("campaign_id") or "")
        if loaded_id and loaded_id != campaign_id:
            raise ValueError("campaign_id does not match existing campaign state")
        state = loaded
        state["campaign_id"] = loaded_id or campaign_id
        state["target_runs"] = campaign_runs
        state["runs"] = list(state.get("runs") or [])
    elif resume_campaign and state_path:
        # Resume flag is harmless if the file doesn't exist yet; start fresh.
        pass

    completed_runs = list(state.get("runs") or [])
    aggregate_rollup: dict[str, Any] = {}
    for rec in completed_runs:
        if isinstance(rec, dict):
            _accumulate_campaign_rollup(aggregate_rollup, dict(rec.get("rollup") or {}))

    stop_reason = str(state.get("stop_reason") or "")
    started_from_count = len(completed_runs)
    for run_index in range(started_from_count, campaign_runs):
        if stop_reason:
            break
        out = run_once_fn()
        run_rollup = dict((out or {}).get("rollup") or {})
        run_summary = dict((out or {}).get("live_pilot_summary") or {})
        run_gate = dict((out or {}).get("promotion_gate_summary") or {})
        sig = str(run_summary.get("submitted_signature") or "")
        completed_runs.append(
            {
                "run_index": run_index,
                "audit_log_path": str((out or {}).get("audit_log_path") or ""),
                "rollup": run_rollup,
                "live_pilot_summary": run_summary,
                "promotion_gate_summary": run_gate,
                "submitted_signature": sig,
            }
        )
        _accumulate_campaign_rollup(aggregate_rollup, run_rollup)
        for alert in _campaign_alert_policy_eval(run_out=out, aggregate_rollup=aggregate_rollup, policy=alert_policy):
            row = {"run_index": run_index, **dict(alert or {})}
            alerts_emitted.append(row)
            if alert_emitter is not None:
                alert_emitter(row)
        stop_decision = stop_evaluator(out)
        if isinstance(stop_decision, dict) and bool(stop_decision.get("stop", False)):
            stop_reason = str(stop_decision.get("reason") or "campaign_stop_evaluator")
            if bool(alert_on_campaign_stop):
                row = {
                    "run_index": run_index,
                    "alert_type": "campaign_hard_stop",
                    "level": "critical",
                    "message": f"Campaign stopped early: {stop_reason}",
                    "details": {"stop_reason": stop_reason},
                }
                alerts_emitted.append(row)
                if alert_emitter is not None:
                    alert_emitter(row)

        state = {
            "campaign_id": state.get("campaign_id") or campaign_id,
            "target_runs": campaign_runs,
            "runs": completed_runs,
            "stop_reason": stop_reason,
        }
        if state_path:
            state_path.write_text(json.dumps(state, sort_keys=True, indent=2), encoding="utf-8")

    aggregate_clean = _cleanup_campaign_rollup_for_output(aggregate_rollup)
    campaign_gate = _evaluate_live_pilot_promotion_gates(aggregate_clean, promotion_gate_config)
    campaign_summary = {
        "campaign_id": state.get("campaign_id") or campaign_id,
        "target_runs": campaign_runs,
        "completed_runs": len(completed_runs),
        "stop_reason": stop_reason or ("" if len(completed_runs) >= campaign_runs else "interrupted"),
        "aggregate_rollup": aggregate_clean,
        "promotion_gate_summary": campaign_gate,
        "alert_summary": _summarize_campaign_alerts(alerts_emitted),
    }
    if bool(alert_on_promotion_gate_fail) and str(campaign_gate.get("status") or "") == "fail":
        row = {
            "alert_type": "promotion_gate_failed",
            "level": "warning",
            "message": "Campaign promotion gate failed.",
            "details": {"failed_checks": list(campaign_gate.get("failed_checks", []) or [])},
        }
        alerts_emitted.append(row)
        if alert_emitter is not None:
            alert_emitter(row)
        campaign_summary["alert_summary"] = _summarize_campaign_alerts(alerts_emitted)
    report = {
        "campaign_summary": campaign_summary,
        "runs": completed_runs,
        "resume_used": bool(resume_campaign),
        "state_path": str(state_path) if state_path else "",
        "alerts": alerts_emitted,
    }
    if str(campaign_report_path or "").strip():
        _write_campaign_report(report, campaign_report_path)
        report["report_path"] = str(campaign_report_path)
    return report


def _rollup_update_from_dispatch(rollup: dict[str, Any], dispatch: dict[str, Any]) -> None:
    reason = str((dispatch or {}).get("reason") or "")
    if reason:
        by_reason = rollup.setdefault("submit_dispatch_by_reason", {})
        by_reason[reason] = int(by_reason.get(reason, 0)) + 1
    if (dispatch or {}).get("submitted_signature"):
        rollup["submitted_signatures"] = int(rollup.get("submitted_signatures", 0)) + 1
    if isinstance((dispatch or {}).get("pause_latch"), dict) and (dispatch["pause_latch"].get("latched") is True):
        rollup["pause_latch_events"] = int(rollup.get("pause_latch_events", 0)) + 1
    if isinstance((dispatch or {}).get("pause_reset"), dict) and (dispatch["pause_reset"].get("reset_applied") is True):
        rollup["pause_reset_events"] = int(rollup.get("pause_reset_events", 0)) + 1
    chain_rec = (dispatch or {}).get("chain_reconciliation")
    if isinstance(chain_rec, dict):
        outcome = str(chain_rec.get("outcome_class") or "")
        if outcome:
            by_outcome = rollup.setdefault("live_reconciliation_outcome_by_class", {})
            by_outcome[outcome] = int(by_outcome.get(outcome, 0)) + 1
        if str((chain_rec.get("normalized_confirmation") or {}).get("normalized_status") or "") == "finalized":
            rollup["live_finalized_count"] = int(rollup.get("live_finalized_count", 0)) + 1
        if int(chain_rec.get("mismatch_flag_count") or 0) > 0:
            rollup["live_reconciliation_mismatch_count"] = int(rollup.get("live_reconciliation_mismatch_count", 0)) + 1


def _maybe_attach_live_chain_reconciliation(payload: dict[str, Any], adapter: LiveExecutionAdapter, cfg: dict[str, Any], audit_log_path: str) -> None:
    md = payload.get("metadata")
    if not isinstance(md, dict):
        return
    dispatch = md.get("submit_dispatch")
    if not isinstance(dispatch, dict):
        return
    if not str(dispatch.get("submitted_signature") or "").strip():
        return
    if isinstance(dispatch.get("chain_reconciliation"), dict):
        return

    rpc = getattr(adapter, "rpc_client", None)
    if not hasattr(rpc, "get_signature_status") or not hasattr(rpc, "get_transaction"):
        dispatch["chain_reconciliation_fetch"] = {
            "enabled": True,
            "status_fetched": False,
            "tx_fetched": False,
            "error": "rpc_client_chain_fetch_not_supported",
        }
        return

    sig = str(dispatch.get("submitted_signature"))
    status_payload = None
    tx_payload = None
    fetch_error = ""
    status_fetched = False
    tx_fetched = False
    try:
        status_payload = rpc.get_signature_status(sig, search_transaction_history=True)
        status_fetched = True
    except Exception as exc:
        fetch_error = f"get_signature_status_error: {exc}"
    if not fetch_error:
        try:
            tx_payload = rpc.get_transaction(
                sig,
                encoding="jsonParsed",
                commitment="confirmed",
                max_supported_transaction_version=0,
            )
            tx_fetched = True
        except Exception as exc:
            fetch_error = f"get_transaction_error: {exc}"

    dispatch["chain_reconciliation_fetch"] = {
        "enabled": True,
        "signature": sig,
        "status_fetched": bool(status_fetched),
        "tx_fetched": bool(tx_fetched),
        "error": str(fetch_error or ""),
    }
    if fetch_error:
        append_audit_event(audit_log_path, "live_submit_chain_reconciliation_fetch", dict(dispatch["chain_reconciliation_fetch"]))
        return

    dispatch["chain_reconciliation"] = reconcile_live_chain_confirmation(
        workflow={
            "final_decision": "submitted",
            "submit_confirm_summary": {"outcome_class": "send_raw_transaction_submitted"},
        },
        signature_status_payload=status_payload if isinstance(status_payload, dict) else {"value": [status_payload]},
        tx_payload=tx_payload if isinstance(tx_payload, dict) else {"result": tx_payload},
        preview_estimates=md.get("estimated_costs"),
        mismatch_thresholds=cfg.get("live_send_chain_reconciliation_thresholds"),
        owner_filter=cfg.get("wallet_public_key"),
    )
    append_audit_event(audit_log_path, "live_submit_chain_reconciliation_fetch", dict(dispatch["chain_reconciliation_fetch"]))
    append_audit_event(audit_log_path, "live_submit_chain_reconciliation", dict(dispatch["chain_reconciliation"]))


def _build_live_pilot_summary(payload: dict[str, Any]) -> dict[str, Any]:
    md = payload.get("metadata") if isinstance(payload, dict) else {}
    dispatch = md.get("submit_dispatch") if isinstance(md, dict) else {}
    if not isinstance(dispatch, dict):
        return {}
    chain = dispatch.get("chain_reconciliation") if isinstance(dispatch.get("chain_reconciliation"), dict) else {}
    summary = {
        "submit_reason": dispatch.get("reason"),
        "submitted_signature": dispatch.get("submitted_signature"),
        "signed_submit_source": dispatch.get("signed_submit_source"),
        "chain_outcome_class": (chain or {}).get("outcome_class"),
        "chain_terminal_reason": (chain or {}).get("terminal_reason"),
        "chain_mismatch_flags": (chain or {}).get("mismatch_flags"),
        "fee_lamports": ((chain or {}).get("settlement_summary") or {}).get("fee_lamports"),
        "tx_signature": ((chain or {}).get("settlement_summary") or {}).get("signature"),
        "solscan_tx_url": (f"https://solscan.io/tx/{dispatch.get('submitted_signature')}" if dispatch.get("submitted_signature") else None),
    }
    economics = dispatch.get("economics") if isinstance(dispatch.get("economics"), dict) else {}
    if economics:
        summary["economics"] = {
            "quote_expected_out_amount_raw": economics.get("quote_expected_out_amount_raw"),
            "settlement_actual_out_amount_raw": economics.get("settlement_actual_out_amount_raw"),
            "realized_slippage_bps_vs_quote": economics.get("realized_slippage_bps_vs_quote"),
            "quote_vs_settlement_mismatch": economics.get("quote_vs_settlement_mismatch"),
            "estimated_total_cost_usd": economics.get("estimated_total_cost_usd"),
            "estimated_slippage_usd": economics.get("estimated_slippage_usd"),
            "estimated_network_fee_usd": economics.get("estimated_network_fee_usd"),
        }
    return summary


def _audit_result_and_dispatch(audit_log_path: str, payload: dict[str, Any]) -> None:
    append_audit_event(audit_log_path, "live_pilot_execution_result", payload)
    md = payload.get("metadata") or {}
    if isinstance(md.get("manual_submit_gate"), dict):
        append_audit_event(audit_log_path, "live_manual_submit_gate", md["manual_submit_gate"])
    if isinstance(md.get("submit_dispatch"), dict):
        append_audit_event(audit_log_path, "live_submit_dispatch", md["submit_dispatch"])
        if isinstance(md["submit_dispatch"].get("pause_latch"), dict):
            append_audit_event(audit_log_path, "live_submit_pause_latch", md["submit_dispatch"]["pause_latch"])
        if isinstance(md["submit_dispatch"].get("pause_reset"), dict):
            append_audit_event(audit_log_path, "live_submit_pause_reset", md["submit_dispatch"]["pause_reset"])


def _build_live_pilot_mechanical_safety_filter_from_config(cfg: dict[str, Any]) -> MechanicalSafetyFilter | None:
    raw = cfg.get("live_pilot_mechanical_safety")
    if raw is None:
        return None
    if raw is True:
        raw = {}
    if not isinstance(raw, dict):
        return None
    enabled = bool(raw.get("enabled", True))
    if not enabled:
        return None
    return MechanicalSafetyFilter(
        require_buy_route=bool(raw.get("require_buy_route", True)),
        require_sell_route=bool(raw.get("require_sell_route", False)),
        require_sanity_probe_route=bool(raw.get("require_sanity_probe_route", False)),
        min_buy_liquidity_usd=(None if raw.get("min_buy_liquidity_usd") is None else float(raw.get("min_buy_liquidity_usd"))),
        max_buy_price_impact_pct=(None if raw.get("max_buy_price_impact_pct") is None else float(raw.get("max_buy_price_impact_pct"))),
        fail_closed_on_check_error=bool(raw.get("fail_closed_on_check_error", True)),
        fail_closed_on_quote_error=(None if raw.get("fail_closed_on_quote_error") is None else bool(raw.get("fail_closed_on_quote_error"))),
        fail_closed_on_mint_error=(None if raw.get("fail_closed_on_mint_error") is None else bool(raw.get("fail_closed_on_mint_error"))),
    )


def _build_live_pilot_volatility_guard_from_config(cfg: dict[str, Any]) -> VolatilityGuard | None:
    raw = cfg.get("live_pilot_volatility_guard")
    if raw is None:
        return None
    if raw is True:
        raw = {"enabled": True}
    if not isinstance(raw, dict):
        return None
    if not bool(raw.get("enabled", False)):
        return None
    return VolatilityGuard(
        enabled=True,
        max_loss_streak_block=(None if raw.get("max_loss_streak_block") is None else int(raw.get("max_loss_streak_block"))),
        loss_streak_derisk_threshold=(None if raw.get("loss_streak_derisk_threshold") is None else int(raw.get("loss_streak_derisk_threshold"))),
        max_session_drawdown_usd_block=(None if raw.get("max_session_drawdown_usd_block") is None else float(raw.get("max_session_drawdown_usd_block"))),
        session_drawdown_derisk_threshold_usd=(
            None if raw.get("session_drawdown_derisk_threshold_usd") is None else float(raw.get("session_drawdown_derisk_threshold_usd"))
        ),
        derisk_size_multiplier=float(raw.get("derisk_size_multiplier", 1.0)),
        derisk_min_usd_size=(None if raw.get("derisk_min_usd_size") is None else float(raw.get("derisk_min_usd_size"))),
    )


def _build_live_pilot_signal_provider_from_args(args, dexscreener_fetcher=None):
    signal_provider_json_path = str(getattr(args, "signal_provider_json_path", "") or "").strip()
    use_dexscreener_signals = bool(getattr(args, "use_dexscreener_signals", False))
    selected = (1 if signal_provider_json_path else 0) + (1 if use_dexscreener_signals else 0)
    if selected > 1:
        raise ValueError("use only one signal input: --signal-provider-json-path or --use-dexscreener-signals")

    if signal_provider_json_path:
        from src.live.signal_provider_file import FileSignalProvider
        return FileSignalProvider.from_path(signal_provider_json_path)

    if use_dexscreener_signals:
        if dexscreener_fetcher is None:
            fetch_url = str(getattr(args, "dexscreener_fetch_url", "") or "").strip()
            fallback_urls: list[str] = []
            fallback_urls_json_path = str(getattr(args, "dexscreener_fallback_urls_json_path", "") or "").strip()
            if fallback_urls_json_path:
                raw = json.loads(Path(fallback_urls_json_path).read_text(encoding="utf-8-sig"))
                if not isinstance(raw, list):
                    raise ValueError("dexscreener fallback urls json must be a list")
                fallback_urls = [str(x).strip() for x in raw if str(x or "").strip()]
            header_entries = list(getattr(args, "dexscreener_header", []) or [])
            headers: dict[str, str] = {}
            user_agent = str(getattr(args, "dexscreener_user_agent", "") or "").strip()
            if user_agent:
                headers["User-Agent"] = user_agent
            for item in header_entries:
                s = str(item or "")
                if ":" not in s:
                    raise ValueError("dexscreener header must be NAME:VALUE")
                k, v = s.split(":", 1)
                k = k.strip()
                v = v.strip()
                if not k:
                    raise ValueError("dexscreener header name cannot be empty")
                headers[k] = v
            if fetch_url:
                dexscreener_fetcher = DexScreenerHttpPairsFetcher(
                    url=fetch_url,
                    fallback_urls=fallback_urls,
                    timeout_seconds=float(getattr(args, "dexscreener_fetch_timeout_seconds", 5.0)),
                    max_attempts=max(1, int(getattr(args, "dexscreener_fetch_max_attempts", 1))),
                    retry_backoff_seconds=float(getattr(args, "dexscreener_fetch_retry_backoff_seconds", 0.0)),
                    max_payload_age_ms=getattr(args, "dexscreener_max_payload_age_ms", None),
                    fail_on_stale_payload=not bool(getattr(args, "dexscreener_allow_stale_payloads", False)),
                    headers=headers,
                )
            else:
                dexscreener_fetcher = lambda: {"pairs": []}
        return DexScreenerSignalProvider(
            dexscreener_fetcher,
            default_usd_size=float(getattr(args, "usd_size", 1.0)),
            chain_id=(getattr(args, "dexscreener_chain_id", "") or None),
            min_liquidity_usd=getattr(args, "dexscreener_min_liquidity_usd", None),
            max_pair_age_seconds=getattr(args, "dexscreener_max_pair_age_seconds", None),
            swallow_fetch_errors=True,
        )
    return None


def _apply_live_pilot_mode_preset(args) -> None:
    mode = str(getattr(args, "mode", "") or "").strip().lower()
    if not mode:
        return

    def _set_default(name: str, value: Any) -> None:
        current = getattr(args, name, None)
        if isinstance(current, str):
            if current == "":
                setattr(args, name, value)
            return
        # Treat explicit parser defaults as overridable by preset.
        parser_defaults = {
            "auto_pilot_window_seconds": 0.0,
            "auto_pilot_max_trades": 1,
            "auto_pilot_poll_interval_seconds": 0.0,
            "signal_require_fresh_seconds": 0.0,
            "signal_max_candidates_per_window": 10,
            "dexscreener_chain_id": "solana",
            "dexscreener_min_liquidity_usd": None,
            "dexscreener_max_pair_age_seconds": None,
            "campaign_runs": 0,
        }
        if name in parser_defaults and current == parser_defaults[name]:
            setattr(args, name, value)

    if mode == "no_send_signal_auto":
        _set_default("auto_pilot_window_seconds", 30.0)
        _set_default("auto_pilot_max_trades", 1)
        _set_default("signal_require_fresh_seconds", 3600.0)
        _set_default("signal_max_candidates_per_window", 5)
        setattr(args, "auto_pilot_stop_on_reconciliation_mismatch", True)
        setattr(args, "auto_pilot_stop_on_reconciliation_inconclusive", True)
        return
    if mode == "no_send_dexscreener_auto":
        _set_default("auto_pilot_window_seconds", 30.0)
        _set_default("auto_pilot_max_trades", 1)
        _set_default("signal_require_fresh_seconds", 3600.0)
        _set_default("signal_max_candidates_per_window", 5)
        _set_default("dexscreener_chain_id", "solana")
        _set_default("dexscreener_min_liquidity_usd", 5000.0)
        _set_default("dexscreener_max_pair_age_seconds", 600.0)
        setattr(args, "use_dexscreener_signals", True)
        setattr(args, "auto_pilot_stop_on_reconciliation_mismatch", True)
        setattr(args, "auto_pilot_stop_on_reconciliation_inconclusive", True)
        return
    if mode == "live_auto_tiny_one_trade":
        _set_default("auto_pilot_window_seconds", 30.0)
        _set_default("auto_pilot_max_trades", 1)
        _set_default("signal_require_fresh_seconds", 3600.0)
        _set_default("signal_max_candidates_per_window", 5)
        setattr(args, "enable_live_auto_submit_window", True)
        setattr(args, "auto_pilot_stop_on_reconciliation_mismatch", True)
        setattr(args, "auto_pilot_stop_on_reconciliation_inconclusive", True)
        return
    if mode == "live_dexscreener_tiny_one_trade":
        _set_default("auto_pilot_window_seconds", 30.0)
        _set_default("auto_pilot_max_trades", 1)
        _set_default("signal_require_fresh_seconds", 3600.0)
        _set_default("signal_max_candidates_per_window", 5)
        _set_default("dexscreener_chain_id", "solana")
        _set_default("dexscreener_min_liquidity_usd", 5000.0)
        _set_default("dexscreener_max_pair_age_seconds", 600.0)
        setattr(args, "use_dexscreener_signals", True)
        setattr(args, "enable_live_auto_submit_window", True)
        setattr(args, "auto_pilot_stop_on_reconciliation_mismatch", True)
        setattr(args, "auto_pilot_stop_on_reconciliation_inconclusive", True)
        return
    if mode == "pilot_campaign_tiny_supervised":
        _set_default("campaign_runs", 3)
        _set_default("auto_pilot_window_seconds", 30.0)
        _set_default("auto_pilot_max_trades", 1)
        _set_default("signal_require_fresh_seconds", 3600.0)
        _set_default("signal_max_candidates_per_window", 5)
        _set_default("dexscreener_chain_id", "solana")
        _set_default("dexscreener_min_liquidity_usd", 5000.0)
        _set_default("dexscreener_max_pair_age_seconds", 600.0)
        setattr(args, "use_dexscreener_signals", True)
        setattr(args, "auto_pilot_stop_on_reconciliation_mismatch", True)
        setattr(args, "auto_pilot_stop_on_reconciliation_inconclusive", True)
        return
    raise ValueError(f"unknown live pilot mode preset: {mode}")


def _validate_live_auto_window_guardrails(
    *,
    adapter_config: dict | None,
    max_auto_trades: int,
    explicit_live_auto_submit_enable: bool = False,
) -> None:
    cfg = dict(adapter_config or {})
    if not bool(cfg.get("live_send_network_enabled", False)):
        return
    if not bool(explicit_live_auto_submit_enable):
        raise ValueError(
            "live auto-window network submits require explicit enable flag: --enable-live-auto-submit-window"
        )
    if int(max_auto_trades) != 1:
        raise ValueError("live auto-window network submits require --auto-pilot-max-trades 1 for S4-M22.4")

    max_orders = cfg.get("live_send_max_orders_per_session")
    if max_orders is None or int(max_orders) > 1:
        raise ValueError("live auto-window network submits require live_send_max_orders_per_session <= 1")

    max_notional = cfg.get("live_send_max_notional_usd_total")
    if max_notional is None or float(max_notional) > 1.0:
        raise ValueError("live auto-window network submits require live_send_max_notional_usd_total <= 1.0")

    if str(cfg.get("manual_submit_mode") or "").strip().lower() != "buy_only":
        raise ValueError("live auto-window network submits require manual_submit_mode=buy_only")


def _build_live_pilot_preflight(args, adapter_config: dict | None = None) -> dict[str, Any]:
    cfg = dict(adapter_config or {})
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []
    auto_window_enabled = float(getattr(args, "auto_pilot_window_seconds", 0.0) or 0.0) > 0

    signal_inputs = []
    if str(getattr(args, "signal_provider_json_path", "") or "").strip():
        signal_inputs.append("signal_provider_json_path")
    if bool(getattr(args, "use_dexscreener_signals", False)):
        signal_inputs.append("dexscreener")
    if str(getattr(args, "candidate_list_json_path", "") or "").strip() or str(getattr(args, "candidate_list_json", "") or "").strip():
        signal_inputs.append("candidate_list")
    if len(signal_inputs) > 1:
        errors.append("Only one auto-window signal input may be selected (signal file, candidate list, or dexscreener).")
    if auto_window_enabled and not signal_inputs:
        info.append("Auto-window enabled with no signal/candidate source; service will use single token CLI inputs.")

    if bool(getattr(args, "use_dexscreener_signals", False)):
        fetch_url = str(getattr(args, "dexscreener_fetch_url", "") or "").strip()
        fallback_urls_json_path = str(getattr(args, "dexscreener_fallback_urls_json_path", "") or "").strip()
        if not fetch_url:
            warnings.append("DexScreener mode enabled without --dexscreener-fetch-url (provider will return zero pairs).")
        if fallback_urls_json_path:
            try:
                raw = json.loads(Path(fallback_urls_json_path).read_text(encoding="utf-8-sig"))
                if not isinstance(raw, list):
                    raise ValueError("fallback file must contain a JSON array")
                fallback_urls = [str(x).strip() for x in raw if str(x or "").strip()]
                if not fallback_urls:
                    warnings.append("DexScreener fallback URLs file is present but contains no usable URLs.")
                seen: set[str] = set()
                dupes = False
                for u in fallback_urls:
                    if u in seen:
                        dupes = True
                        break
                    seen.add(u)
                if dupes:
                    warnings.append("DexScreener fallback URLs file contains duplicates.")
                if fetch_url and fetch_url in seen:
                    warnings.append("DexScreener primary fetch URL is duplicated in fallback URLs file.")
                info.append(f"DexScreener fallback URLs configured: {len(fallback_urls)}")
            except Exception as exc:
                errors.append(f"Invalid DexScreener fallback URLs file: {exc}")
        if str(getattr(args, "dexscreener_chain_id", "") or "").strip().lower() != "solana":
            warnings.append("DexScreener chain filter is not 'solana'.")
        if bool(cfg.get("live_send_network_enabled", False)):
            if getattr(args, "dexscreener_min_liquidity_usd", None) is None:
                warnings.append("Live DexScreener auto mode should set --dexscreener-min-liquidity-usd.")
            if getattr(args, "dexscreener_max_pair_age_seconds", None) is None:
                warnings.append("Live DexScreener auto mode should set --dexscreener-max-pair-age-seconds.")

    if not bool(cfg.get("live_send_network_enabled", False)):
        info.append("Network send gate is OFF (no-send safe mode).")
    if auto_window_enabled:
        try:
            _validate_live_auto_window_guardrails(
                adapter_config=cfg,
                max_auto_trades=int(getattr(args, "auto_pilot_max_trades", 1) or 1),
                explicit_live_auto_submit_enable=bool(getattr(args, "enable_live_auto_submit_window", False)),
            )
        except Exception as exc:
            errors.append(str(exc))

    effective = {
        "mode": str(getattr(args, "mode", "") or ""),
        "auto_window_enabled": auto_window_enabled,
        "auto_pilot_window_seconds": float(getattr(args, "auto_pilot_window_seconds", 0.0) or 0.0),
        "auto_pilot_max_trades": int(getattr(args, "auto_pilot_max_trades", 1) or 1),
        "signal_inputs": signal_inputs,
        "live_send_network_enabled": bool(cfg.get("live_send_network_enabled", False)),
        "live_send_max_orders_per_session": cfg.get("live_send_max_orders_per_session"),
        "live_send_max_notional_usd_total": cfg.get("live_send_max_notional_usd_total"),
        "manual_submit_mode": cfg.get("manual_submit_mode"),
        "use_dexscreener_signals": bool(getattr(args, "use_dexscreener_signals", False)),
        "dexscreener_fetch_url_present": bool(str(getattr(args, "dexscreener_fetch_url", "") or "").strip()),
        "dexscreener_fallback_urls_json_path_present": bool(str(getattr(args, "dexscreener_fallback_urls_json_path", "") or "").strip()),
        "signal_provider_json_path": str(getattr(args, "signal_provider_json_path", "") or ""),
        "candidate_list_json_path": str(getattr(args, "candidate_list_json_path", "") or ""),
    }
    return {"ready": len(errors) == 0, "errors": errors, "warnings": warnings, "info": info, "effective": effective}


def _format_human_live_pilot_summary(out: dict[str, Any]) -> str:
    rollup = dict((out or {}).get("rollup") or {})
    summary = dict((out or {}).get("live_pilot_summary") or {})
    auto = dict(rollup.get("auto_window") or {})
    lines = [
        "Live Pilot Summary",
        f"signals: seen={int(rollup.get('signals_seen', 0))} accepted={int(rollup.get('signals_accepted', 0))} rejected={int(rollup.get('signals_rejected', 0))}",
        f"candidates: seen={int(rollup.get('candidates_seen', 0))} attempted={int(rollup.get('candidates_attempted', 0))} submitted={int(rollup.get('candidates_submitted', 0))}",
        f"submits: submitted_signatures={int(rollup.get('submitted_signatures', 0))} reasons={json.dumps(rollup.get('submit_dispatch_by_reason', {}), sort_keys=True)}",
    ]
    if auto:
        lines.append(
            f"auto_window: stop_reason={auto.get('stop_reason')} cycles={int(auto.get('cycles_completed', 0))} trades_submitted={int(auto.get('trades_submitted', 0))}"
        )
    if int(rollup.get("economics_samples_count", 0)) > 0:
        lines.append(
            "economics: "
            f"samples={int(rollup.get('economics_samples_count', 0))} "
            f"fee_lamports_total={int(rollup.get('fee_lamports_total', 0))} "
            f"avg_slippage_bps={rollup.get('avg_realized_slippage_bps')} "
            f"worst_slippage_bps={rollup.get('worst_realized_slippage_bps')} "
            f"quote_mismatches={int(rollup.get('quote_vs_settlement_mismatch_count', 0))}"
        )
    provider_metrics = dict(rollup.get("signal_provider_metrics") or {})
    if provider_metrics:
        lines.append(
            "signal_provider: "
            f"retries={int(provider_metrics.get('fetch_retry_events', 0))} "
            f"transport_errors={int(provider_metrics.get('fetch_transport_errors', 0))} "
            f"fallback_selected={int(provider_metrics.get('fetch_fallback_selected_events', 0))} "
            f"endpoint_failures={int(provider_metrics.get('fetch_endpoint_failure_events', 0))}"
        )
        last_meta = dict(provider_metrics.get("last_fetch_meta") or {})
        selected_url = str(last_meta.get("selected_url") or "")
        if selected_url:
            lines.append(f"signal_provider_last_source: {selected_url}")
    if summary:
        lines.append(
            f"last_submit: reason={summary.get('submit_reason')} signature={summary.get('submitted_signature') or '-'} chain={summary.get('chain_outcome_class') or '-'}"
        )
        econ = dict(summary.get("economics") or {})
        if econ:
            lines.append(
                "last_submit_econ: "
                f"realized_slippage_bps={econ.get('realized_slippage_bps_vs_quote')} "
                f"quote_mismatch={econ.get('quote_vs_settlement_mismatch')} "
                f"fee_lamports={summary.get('fee_lamports')}"
            )
        if summary.get("solscan_tx_url"):
            lines.append(f"solscan: {summary.get('solscan_tx_url')}")
    gate = dict((out or {}).get("promotion_gate_summary") or {})
    if gate:
        lines.append(
            f"promotion_gate: status={gate.get('status')} ready={bool(gate.get('ready_to_promote', False))} failed={','.join(list(gate.get('failed_checks', []) or [])) or '-'}"
        )
    return "\n".join(lines)


def run_live_pilot_service_once(
    *,
    token_address: str,
    symbol: str,
    entry_price: float,
    usd_size: float,
    audit_log_dir: str = "data/exports",
    adapter_config: dict | None = None,
    rpc_client=None,
    dex_executor=None,
    rpc_transport=None,
    dex_quote_transport=None,
    dex_swap_transport=None,
) -> dict:
    cfg = dict(adapter_config or {})
    cfg.setdefault("live_enabled", True)
    cfg.setdefault("pilot_mode", True)
    cfg.setdefault("audit_log_path", str(Path(audit_log_dir) / "pilot_live_service_audit.jsonl"))
    adapter = LiveExecutionAdapter(
        cfg,
        rpc_client=rpc_client,
        dex_executor=dex_executor,
        rpc_transport=rpc_transport,
        dex_quote_transport=dex_quote_transport,
        dex_swap_transport=dex_swap_transport,
    )

    audit_log_path = build_audit_log_path(audit_log_dir, prefix="live_pilot_service")
    append_audit_event(audit_log_path, "live_pilot_service_started", {"mode": "one_shot"})

    result = adapter.buy(str(token_address), str(symbol), float(entry_price), float(usd_size))
    payload = {
        "ok": bool(result.ok),
        "action": result.action,
        "message": result.message,
        "metadata": dict(result.metadata or {}),
    }
    _maybe_attach_live_chain_reconciliation(payload, adapter, cfg, audit_log_path)
    _maybe_attach_live_submit_economics(payload, cfg)
    _audit_result_and_dispatch(audit_log_path, payload)
    md = payload["metadata"]

    rollup = {
        "runs": 1,
        "submit_dispatch_by_reason": {},
        "submitted_signatures": 0,
        "pause_latch_events": 0,
        "pause_reset_events": 0,
        "economics_samples_count": 0,
        "fee_lamports_total": 0,
        "quote_vs_settlement_mismatch_count": 0,
    }
    _rollup_update_from_payload(rollup, payload)
    live_summary = _build_live_pilot_summary(payload)
    promotion_gate_summary = _evaluate_live_pilot_promotion_gates(rollup, cfg.get("live_pilot_promotion_gates"))
    append_audit_event(audit_log_path, "live_pilot_service_summary", {"summary": live_summary})
    append_audit_event(audit_log_path, "live_pilot_promotion_gate_evaluation", {"promotion_gate_summary": promotion_gate_summary})
    append_audit_event(
        audit_log_path,
        "live_pilot_service_completed",
        {"rollup": rollup, "live_pilot_summary": live_summary, "promotion_gate_summary": promotion_gate_summary},
    )
    return {
        "audit_log_path": audit_log_path,
        "result": payload,
        "rollup": rollup,
        "live_pilot_summary": live_summary,
        "promotion_gate_summary": promotion_gate_summary,
    }


def run_live_pilot_service_loop(
    *,
    token_address: str,
    symbol: str,
    entry_price: float,
    usd_size: float,
    iterations: int,
    audit_log_dir: str = "data/exports",
    adapter_config: dict | None = None,
    rpc_client=None,
    dex_executor=None,
    rpc_transport=None,
    dex_quote_transport=None,
    dex_swap_transport=None,
) -> dict:
    iterations = int(iterations)
    if iterations <= 0:
        raise ValueError("iterations must be > 0")
    cfg = dict(adapter_config or {})
    cfg.setdefault("live_enabled", True)
    cfg.setdefault("pilot_mode", True)
    cfg.setdefault("audit_log_path", str(Path(audit_log_dir) / "pilot_live_service_audit.jsonl"))
    adapter = LiveExecutionAdapter(
        cfg,
        rpc_client=rpc_client,
        dex_executor=dex_executor,
        rpc_transport=rpc_transport,
        dex_quote_transport=dex_quote_transport,
        dex_swap_transport=dex_swap_transport,
    )

    audit_log_path = build_audit_log_path(audit_log_dir, prefix="live_pilot_service_loop")
    append_audit_event(audit_log_path, "live_pilot_service_started", {"mode": "loop", "iterations": iterations})

    rollup = {
        "runs": int(iterations),
        "submit_dispatch_by_reason": {},
        "submitted_signatures": 0,
        "pause_latch_events": 0,
        "pause_reset_events": 0,
        "cycles_completed": 0,
    }
    cycle_results = []
    for i in range(iterations):
        result = adapter.buy(str(token_address), str(symbol), float(entry_price), float(usd_size))
        payload = {
            "ok": bool(result.ok),
            "action": result.action,
            "message": result.message,
            "metadata": dict(result.metadata or {}),
            "iteration": i,
        }
        _maybe_attach_live_chain_reconciliation(payload, adapter, cfg, audit_log_path)
        _maybe_attach_live_submit_economics(payload, cfg)
        _audit_result_and_dispatch(audit_log_path, payload)
        dispatch = payload["metadata"].get("submit_dispatch") if isinstance(payload.get("metadata"), dict) else {}
        _rollup_update_from_payload(rollup, payload)
        append_audit_event(
            audit_log_path,
            "live_pilot_service_cycle_completed",
            {
                "iteration": i,
                "submit_dispatch_reason": (dispatch or {}).get("reason") if isinstance(dispatch, dict) else None,
                "pause_latched": bool((dispatch or {}).get("pause_latched", False)) if isinstance(dispatch, dict) else False,
                "chain_outcome_class": ((dispatch or {}).get("chain_reconciliation") or {}).get("outcome_class") if isinstance(dispatch, dict) else None,
            },
        )
        cycle_results.append(
            {
                "iteration": i,
                "submit_dispatch_reason": (dispatch or {}).get("reason") if isinstance(dispatch, dict) else None,
                "chain_outcome_class": ((dispatch or {}).get("chain_reconciliation") or {}).get("outcome_class") if isinstance(dispatch, dict) else None,
            }
        )
        rollup["cycles_completed"] = int(rollup.get("cycles_completed", 0)) + 1

    promotion_gate_summary = _evaluate_live_pilot_promotion_gates(rollup, cfg.get("live_pilot_promotion_gates"))
    append_audit_event(audit_log_path, "live_pilot_promotion_gate_evaluation", {"promotion_gate_summary": promotion_gate_summary})
    append_audit_event(audit_log_path, "live_pilot_service_completed", {"rollup": rollup, "promotion_gate_summary": promotion_gate_summary})
    return {"audit_log_path": audit_log_path, "rollup": rollup, "cycles": cycle_results, "promotion_gate_summary": promotion_gate_summary}


def run_live_pilot_auto_window(
    *,
    token_address: str,
    symbol: str,
    entry_price: float,
    usd_size: float,
    window_seconds: float,
    max_auto_trades: int = 1,
    poll_interval_seconds: float = 0.0,
    stop_on_reconciliation_mismatch: bool = True,
    stop_on_reconciliation_inconclusive: bool = True,
    audit_log_dir: str = "data/exports",
    adapter_config: dict | None = None,
    rpc_client=None,
    dex_executor=None,
    rpc_transport=None,
    dex_quote_transport=None,
    dex_swap_transport=None,
    now_fn=None,
    sleep_fn=None,
) -> dict:
    window_seconds = float(window_seconds)
    if window_seconds <= 0:
        raise ValueError("window_seconds must be > 0")
    max_auto_trades = int(max_auto_trades)
    if max_auto_trades <= 0:
        raise ValueError("max_auto_trades must be > 0")
    poll_interval_seconds = float(poll_interval_seconds)
    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must be >= 0")

    now_fn = now_fn or time.time
    sleep_fn = sleep_fn or time.sleep

    cfg = dict(adapter_config or {})
    cfg.setdefault("live_enabled", True)
    cfg.setdefault("pilot_mode", True)
    cfg.setdefault("audit_log_path", str(Path(audit_log_dir) / "pilot_live_service_audit.jsonl"))
    adapter = LiveExecutionAdapter(
        cfg,
        rpc_client=rpc_client,
        dex_executor=dex_executor,
        rpc_transport=rpc_transport,
        dex_quote_transport=dex_quote_transport,
        dex_swap_transport=dex_swap_transport,
    )

    audit_log_path = build_audit_log_path(audit_log_dir, prefix="live_pilot_service_auto_window")
    started_at = float(now_fn())
    deadline = started_at + window_seconds
    append_audit_event(
        audit_log_path,
        "live_pilot_service_started",
        {
            "mode": "auto_window",
            "window_seconds": window_seconds,
            "max_auto_trades": max_auto_trades,
            "poll_interval_seconds": poll_interval_seconds,
        },
    )

    rollup = {
        "runs": 0,
        "submit_dispatch_by_reason": {},
        "submitted_signatures": 0,
        "pause_latch_events": 0,
        "pause_reset_events": 0,
        "live_reconciliation_outcome_by_class": {},
        "live_finalized_count": 0,
        "live_reconciliation_mismatch_count": 0,
        "auto_window": {
            "window_seconds": window_seconds,
            "max_auto_trades": max_auto_trades,
            "poll_interval_seconds": poll_interval_seconds,
            "stop_reason": "",
            "trades_submitted": 0,
            "cycles_completed": 0,
        },
    }
    cycles: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    while True:
        now_val = float(now_fn())
        if now_val >= deadline:
            rollup["auto_window"]["stop_reason"] = "window_elapsed"
            break
        if int(rollup["auto_window"]["trades_submitted"]) >= max_auto_trades:
            rollup["auto_window"]["stop_reason"] = "max_auto_trades_reached"
            break

        result = adapter.buy(str(token_address), str(symbol), float(entry_price), float(usd_size))
        payload = {
            "ok": bool(result.ok),
            "action": result.action,
            "message": result.message,
            "metadata": dict(result.metadata or {}),
            "auto_window_cycle": int(rollup["auto_window"]["cycles_completed"]),
        }
        _maybe_attach_live_chain_reconciliation(payload, adapter, cfg, audit_log_path)
        _maybe_attach_live_submit_economics(payload, cfg)
        _audit_result_and_dispatch(audit_log_path, payload)
        dispatch = payload["metadata"].get("submit_dispatch") if isinstance(payload.get("metadata"), dict) else {}
        _rollup_update_from_payload(rollup, payload)
        rollup["runs"] = int(rollup.get("runs", 0)) + 1
        rollup["auto_window"]["cycles_completed"] = int(rollup["auto_window"].get("cycles_completed", 0)) + 1
        if isinstance(dispatch, dict) and str(dispatch.get("submitted_signature") or ""):
            rollup["auto_window"]["trades_submitted"] = int(rollup["auto_window"].get("trades_submitted", 0)) + 1

        summary = _build_live_pilot_summary(payload)
        if summary:
            summaries.append(summary)
        append_audit_event(
            audit_log_path,
            "live_pilot_service_cycle_completed",
            {
                "mode": "auto_window",
                "cycle": payload["auto_window_cycle"],
                "submit_dispatch_reason": (dispatch or {}).get("reason") if isinstance(dispatch, dict) else None,
                "chain_outcome_class": ((dispatch or {}).get("chain_reconciliation") or {}).get("outcome_class") if isinstance(dispatch, dict) else None,
            },
        )
        cycles.append(
            {
                "cycle": payload["auto_window_cycle"],
                "submit_dispatch_reason": (dispatch or {}).get("reason") if isinstance(dispatch, dict) else None,
                "chain_outcome_class": ((dispatch or {}).get("chain_reconciliation") or {}).get("outcome_class") if isinstance(dispatch, dict) else None,
            }
        )

        chain_outcome = str(((dispatch or {}).get("chain_reconciliation") or {}).get("outcome_class") or "") if isinstance(dispatch, dict) else ""
        if stop_on_reconciliation_mismatch and chain_outcome == "live_reconciliation_mismatch":
            rollup["auto_window"]["stop_reason"] = "reconciliation_mismatch"
            break
        if stop_on_reconciliation_inconclusive and chain_outcome == "live_confirmation_inconclusive":
            rollup["auto_window"]["stop_reason"] = "reconciliation_inconclusive"
            break
        if int(rollup["auto_window"]["trades_submitted"]) >= max_auto_trades:
            rollup["auto_window"]["stop_reason"] = "max_auto_trades_reached"
            break

        if poll_interval_seconds > 0:
            sleep_fn(poll_interval_seconds)

    final_summary = summaries[-1] if summaries else {}
    append_audit_event(
        audit_log_path,
        "live_pilot_service_summary",
        {"summary": final_summary, "auto_window": dict(rollup.get("auto_window") or {})},
    )
    promotion_gate_summary = _evaluate_live_pilot_promotion_gates(rollup, cfg.get("live_pilot_promotion_gates"))
    append_audit_event(audit_log_path, "live_pilot_promotion_gate_evaluation", {"promotion_gate_summary": promotion_gate_summary})
    append_audit_event(
        audit_log_path,
        "live_pilot_service_completed",
        {"rollup": rollup, "live_pilot_summary": final_summary, "promotion_gate_summary": promotion_gate_summary},
    )
    return {
        "audit_log_path": audit_log_path,
        "rollup": rollup,
        "cycles": cycles,
        "live_pilot_summary": final_summary,
        "promotion_gate_summary": promotion_gate_summary,
    }


def run_live_pilot_auto_window_candidates(
    *,
    candidates: list[dict[str, Any]],
    window_seconds: float,
    max_auto_trades: int = 1,
    poll_interval_seconds: float = 0.0,
    stop_on_reconciliation_mismatch: bool = True,
    stop_on_reconciliation_inconclusive: bool = True,
    audit_log_dir: str = "data/exports",
    adapter_config: dict | None = None,
    rpc_client=None,
    dex_executor=None,
    rpc_transport=None,
    dex_quote_transport=None,
    dex_swap_transport=None,
    mechanical_safety_filter=None,
    volatility_guard=None,
    now_fn=None,
    sleep_fn=None,
) -> dict:
    window_seconds = float(window_seconds)
    if window_seconds <= 0:
        raise ValueError("window_seconds must be > 0")
    max_auto_trades = int(max_auto_trades)
    if max_auto_trades <= 0:
        raise ValueError("max_auto_trades must be > 0")
    poll_interval_seconds = float(poll_interval_seconds)
    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must be >= 0")
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")

    now_fn = now_fn or time.time
    sleep_fn = sleep_fn or time.sleep

    cfg = dict(adapter_config or {})
    cfg.setdefault("live_enabled", True)
    cfg.setdefault("pilot_mode", True)
    cfg.setdefault("audit_log_path", str(Path(audit_log_dir) / "pilot_live_service_audit.jsonl"))
    adapter = LiveExecutionAdapter(
        cfg,
        rpc_client=rpc_client,
        dex_executor=dex_executor,
        rpc_transport=rpc_transport,
        dex_quote_transport=dex_quote_transport,
        dex_swap_transport=dex_swap_transport,
    )

    audit_log_path = build_audit_log_path(audit_log_dir, prefix="live_pilot_service_auto_candidates")
    started_at = float(now_fn())
    deadline = started_at + window_seconds
    append_audit_event(
        audit_log_path,
        "live_pilot_service_started",
        {
            "mode": "auto_window_candidates",
            "window_seconds": window_seconds,
            "max_auto_trades": max_auto_trades,
            "poll_interval_seconds": poll_interval_seconds,
            "candidate_count": len(candidates),
        },
    )

    rollup = {
        "runs": 0,
        "submit_dispatch_by_reason": {},
        "submitted_signatures": 0,
        "pause_latch_events": 0,
        "pause_reset_events": 0,
        "live_reconciliation_outcome_by_class": {},
        "live_finalized_count": 0,
        "live_reconciliation_mismatch_count": 0,
        "auto_window": {
            "mode": "candidates",
            "window_seconds": window_seconds,
            "max_auto_trades": max_auto_trades,
            "poll_interval_seconds": poll_interval_seconds,
            "stop_reason": "",
            "trades_submitted": 0,
            "cycles_completed": 0,
        },
        "candidates_seen": 0,
        "candidates_attempted": 0,
        "candidates_submitted": 0,
        "candidate_submit_reason_by_reason": {},
        "candidate_skip_reason_by_reason": {},
        "mechanical_blocked_by_reason": {},
        "volatility_guard_blocked_by_reason": {},
        "volatility_guard_derisked_count": 0,
    }
    cycles: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for idx, candidate in enumerate(candidates):
        now_val = float(now_fn())
        if now_val >= deadline:
            rollup["auto_window"]["stop_reason"] = "window_elapsed"
            break
        if int(rollup["auto_window"]["trades_submitted"]) >= max_auto_trades:
            rollup["auto_window"]["stop_reason"] = "max_auto_trades_reached"
            break

        token_address = str((candidate or {}).get("token_address") or "").strip()
        symbol = str((candidate or {}).get("symbol") or "").strip() or token_address[:6] or "UNK"
        if not token_address:
            rollup["candidates_seen"] = int(rollup.get("candidates_seen", 0)) + 1
            by_reason = rollup.setdefault("candidate_submit_reason_by_reason", {})
            by_reason["invalid_candidate_missing_token_address"] = int(by_reason.get("invalid_candidate_missing_token_address", 0)) + 1
            append_audit_event(
                audit_log_path,
                "live_pilot_service_candidate_skipped",
                {"candidate_index": idx, "reason": "invalid_candidate_missing_token_address", "candidate": candidate},
            )
            continue

        rollup["candidates_seen"] = int(rollup.get("candidates_seen", 0)) + 1
        rollup["candidates_attempted"] = int(rollup.get("candidates_attempted", 0)) + 1
        cand_entry_price = float((candidate or {}).get("entry_price", 0.0) or 0.0)
        if cand_entry_price <= 0:
            cand_entry_price = 1.0
        cand_usd_size = float((candidate or {}).get("usd_size", 0.0) or 0.0)
        if cand_usd_size <= 0:
            cand_usd_size = 1.0
        cand_metadata = dict((candidate or {}).get("metadata") or {})

        if mechanical_safety_filter is not None and hasattr(mechanical_safety_filter, "assess"):
            mech_signal = TradeSignal(
                token_address=token_address,
                symbol=symbol,
                entry_price=cand_entry_price,
                usd_size=cand_usd_size,
                metadata=cand_metadata,
            )
            mech = mechanical_safety_filter.assess(mech_signal)
            mech_payload = {
                "candidate_index": idx,
                "token_address": token_address,
                "symbol": symbol,
                "allowed": bool(getattr(mech, "allowed", False)),
                "primary_reason": str(getattr(mech, "primary_reason", "") or ""),
                "reasons": list(getattr(mech, "reasons", []) or []),
                "details": dict(getattr(mech, "details", {}) or {}),
            }
            append_audit_event(audit_log_path, "live_candidate_mechanical_safety", mech_payload)
            if not bool(getattr(mech, "allowed", False)):
                reason = str(getattr(mech, "primary_reason", "") or "mechanical_blocked")
                skip_by_reason = rollup.setdefault("candidate_skip_reason_by_reason", {})
                skip_by_reason[reason] = int(skip_by_reason.get(reason, 0)) + 1
                mech_by_reason = rollup.setdefault("mechanical_blocked_by_reason", {})
                mech_by_reason[reason] = int(mech_by_reason.get(reason, 0)) + 1
                continue

        if volatility_guard is not None and hasattr(volatility_guard, "assess"):
            vdec = volatility_guard.assess(
                token_address=token_address,
                symbol=symbol,
                requested_usd_size=cand_usd_size,
            )
            vol_payload = {
                "candidate_index": idx,
                "token_address": token_address,
                "symbol": symbol,
                "allowed": bool(getattr(vdec, "allowed", False)),
                "reason": str(getattr(vdec, "reason", "") or ""),
                "derisk_applied": bool(getattr(vdec, "derisk_applied", False)),
                "adjusted_usd_size": getattr(vdec, "adjusted_usd_size", None),
                "details": dict(getattr(vdec, "details", {}) or {}),
            }
            append_audit_event(audit_log_path, "live_candidate_volatility_guard", vol_payload)
            if not bool(getattr(vdec, "allowed", False)):
                reason = str(getattr(vdec, "reason", "") or "volatility_guard_blocked")
                skip_by_reason = rollup.setdefault("candidate_skip_reason_by_reason", {})
                skip_by_reason[reason] = int(skip_by_reason.get(reason, 0)) + 1
                vg_by_reason = rollup.setdefault("volatility_guard_blocked_by_reason", {})
                vg_by_reason[reason] = int(vg_by_reason.get(reason, 0)) + 1
                continue
            if bool(getattr(vdec, "derisk_applied", False)) and getattr(vdec, "adjusted_usd_size", None) is not None:
                cand_usd_size = float(vdec.adjusted_usd_size)
                rollup["volatility_guard_derisked_count"] = int(rollup.get("volatility_guard_derisked_count", 0)) + 1

        result = adapter.buy(token_address, symbol, cand_entry_price, cand_usd_size)
        payload = {
            "ok": bool(result.ok),
            "action": result.action,
            "message": result.message,
            "metadata": dict(result.metadata or {}),
            "auto_window_cycle": int(rollup["auto_window"]["cycles_completed"]),
            "candidate_index": idx,
            "candidate": {"token_address": token_address, "symbol": symbol, "entry_price": cand_entry_price, "usd_size": cand_usd_size},
        }
        _maybe_attach_live_chain_reconciliation(payload, adapter, cfg, audit_log_path)
        _maybe_attach_live_submit_economics(payload, cfg)
        _audit_result_and_dispatch(audit_log_path, payload)
        dispatch = payload["metadata"].get("submit_dispatch") if isinstance(payload.get("metadata"), dict) else {}
        dispatch_reason = str((dispatch or {}).get("reason") or "") if isinstance(dispatch, dict) else ""
        _rollup_update_from_payload(rollup, payload)
        if dispatch_reason:
            by_reason = rollup.setdefault("candidate_submit_reason_by_reason", {})
            by_reason[dispatch_reason] = int(by_reason.get(dispatch_reason, 0)) + 1
        rollup["runs"] = int(rollup.get("runs", 0)) + 1
        rollup["auto_window"]["cycles_completed"] = int(rollup["auto_window"].get("cycles_completed", 0)) + 1
        if isinstance(dispatch, dict) and str(dispatch.get("submitted_signature") or ""):
            rollup["auto_window"]["trades_submitted"] = int(rollup["auto_window"].get("trades_submitted", 0)) + 1
            rollup["candidates_submitted"] = int(rollup.get("candidates_submitted", 0)) + 1

        summary = _build_live_pilot_summary(payload)
        if summary:
            summaries.append(summary)
        append_audit_event(
            audit_log_path,
            "live_pilot_service_cycle_completed",
            {
                "mode": "auto_window_candidates",
                "cycle": payload["auto_window_cycle"],
                "candidate_index": idx,
                "token_address": token_address,
                "symbol": symbol,
                "submit_dispatch_reason": dispatch_reason or None,
                "chain_outcome_class": ((dispatch or {}).get("chain_reconciliation") or {}).get("outcome_class") if isinstance(dispatch, dict) else None,
            },
        )
        cycles.append(
            {
                "cycle": payload["auto_window_cycle"],
                "candidate_index": idx,
                "token_address": token_address,
                "symbol": symbol,
                "submit_dispatch_reason": dispatch_reason or None,
                "chain_outcome_class": ((dispatch or {}).get("chain_reconciliation") or {}).get("outcome_class") if isinstance(dispatch, dict) else None,
            }
        )

        chain_outcome = str(((dispatch or {}).get("chain_reconciliation") or {}).get("outcome_class") or "") if isinstance(dispatch, dict) else ""
        if stop_on_reconciliation_mismatch and chain_outcome == "live_reconciliation_mismatch":
            rollup["auto_window"]["stop_reason"] = "reconciliation_mismatch"
            break
        if stop_on_reconciliation_inconclusive and chain_outcome == "live_confirmation_inconclusive":
            rollup["auto_window"]["stop_reason"] = "reconciliation_inconclusive"
            break
        if int(rollup["auto_window"]["trades_submitted"]) >= max_auto_trades:
            rollup["auto_window"]["stop_reason"] = "max_auto_trades_reached"
            break

        if poll_interval_seconds > 0:
            sleep_fn(poll_interval_seconds)

    if not str(rollup["auto_window"].get("stop_reason") or ""):
        rollup["auto_window"]["stop_reason"] = "candidates_exhausted"

    final_summary = summaries[-1] if summaries else {}
    append_audit_event(
        audit_log_path,
        "live_pilot_service_summary",
        {"summary": final_summary, "auto_window": dict(rollup.get("auto_window") or {}), "candidates_seen": rollup.get("candidates_seen", 0)},
    )
    promotion_gate_summary = _evaluate_live_pilot_promotion_gates(rollup, cfg.get("live_pilot_promotion_gates"))
    append_audit_event(audit_log_path, "live_pilot_promotion_gate_evaluation", {"promotion_gate_summary": promotion_gate_summary})
    append_audit_event(
        audit_log_path,
        "live_pilot_service_completed",
        {"rollup": rollup, "live_pilot_summary": final_summary, "promotion_gate_summary": promotion_gate_summary},
    )
    return {
        "audit_log_path": audit_log_path,
        "rollup": rollup,
        "cycles": cycles,
        "live_pilot_summary": final_summary,
        "promotion_gate_summary": promotion_gate_summary,
    }


def run_live_pilot_auto_window_from_signal_provider(
    *,
    signal_provider: SignalProvider,
    window_seconds: float,
    max_auto_trades: int = 1,
    max_candidates_per_window: int = 10,
    poll_interval_seconds: float = 0.0,
    require_fresh_signal_seconds: float | None = None,
    stop_on_reconciliation_mismatch: bool = True,
    stop_on_reconciliation_inconclusive: bool = True,
    audit_log_dir: str = "data/exports",
    adapter_config: dict | None = None,
    rpc_client=None,
    dex_executor=None,
    rpc_transport=None,
    dex_quote_transport=None,
    dex_swap_transport=None,
    mechanical_safety_filter=None,
    volatility_guard=None,
    now_fn=None,
    sleep_fn=None,
) -> dict:
    if signal_provider is None or not hasattr(signal_provider, "get_next_signal"):
        raise ValueError("signal_provider with get_next_signal() is required")
    max_candidates_per_window = int(max_candidates_per_window)
    if max_candidates_per_window <= 0:
        raise ValueError("max_candidates_per_window must be > 0")

    now_fn = now_fn or time.time
    candidates: list[dict[str, Any]] = []
    signal_rollup = {
        "signals_seen": 0,
        "signals_accepted": 0,
        "signals_rejected": 0,
        "signal_rejected_by_reason": {},
        "provider_metrics": {
            "fetch_retry_events": 0,
            "fetch_stale_payload_events": 0,
            "fetch_transport_errors": 0,
            "fetch_fallback_selected_events": 0,
            "fetch_endpoint_failure_events": 0,
            "last_error": "",
            "last_fetch_meta": {},
        },
    }

    def _consume_provider_metrics() -> None:
        if not hasattr(signal_provider, "consume_runtime_metrics_delta"):
            return
        try:
            delta = signal_provider.consume_runtime_metrics_delta()
        except Exception:
            return
        if not isinstance(delta, dict):
            return
        pm = signal_rollup.setdefault("provider_metrics", {})
        pm["fetch_retry_events"] = int(pm.get("fetch_retry_events", 0)) + int(delta.get("fetch_retry_events", 0) or 0)
        pm["fetch_stale_payload_events"] = int(pm.get("fetch_stale_payload_events", 0)) + int(delta.get("fetch_stale_payload_events", 0) or 0)
        pm["fetch_transport_errors"] = int(pm.get("fetch_transport_errors", 0)) + int(delta.get("fetch_transport_errors", 0) or 0)
        pm["fetch_fallback_selected_events"] = int(pm.get("fetch_fallback_selected_events", 0)) + int(
            delta.get("fetch_fallback_selected_events", 0) or 0
        )
        pm["fetch_endpoint_failure_events"] = int(pm.get("fetch_endpoint_failure_events", 0)) + int(
            delta.get("fetch_endpoint_failure_events", 0) or 0
        )
        if delta.get("last_error") is not None:
            pm["last_error"] = str(delta.get("last_error") or "")
        if isinstance(delta.get("last_fetch_meta"), dict):
            pm["last_fetch_meta"] = dict(delta.get("last_fetch_meta") or {})
        if isinstance(delta.get("last_payload_stats"), dict):
            pm["last_payload_stats"] = dict(delta.get("last_payload_stats") or {})

    for _ in range(max_candidates_per_window):
        sig = signal_provider.get_next_signal()
        _consume_provider_metrics()
        if sig is None:
            break
        signal_rollup["signals_seen"] = int(signal_rollup.get("signals_seen", 0)) + 1
        if not isinstance(sig, TradeSignal):
            signal_rollup["signals_rejected"] = int(signal_rollup.get("signals_rejected", 0)) + 1
            by_reason = signal_rollup.setdefault("signal_rejected_by_reason", {})
            by_reason["invalid_signal_type"] = int(by_reason.get("invalid_signal_type", 0)) + 1
            continue

        md = dict(sig.metadata or {})
        if require_fresh_signal_seconds is not None:
            observed_ts = md.get("observed_at_unix_ms", md.get("discovered_at_unix_ms"))
            if observed_ts is None:
                signal_rollup["signals_rejected"] = int(signal_rollup.get("signals_rejected", 0)) + 1
                by_reason = signal_rollup.setdefault("signal_rejected_by_reason", {})
                by_reason["missing_signal_timestamp"] = int(by_reason.get("missing_signal_timestamp", 0)) + 1
                continue
            age_seconds = max(0.0, (float(now_fn()) - (float(observed_ts) / 1000.0)))
            if age_seconds > float(require_fresh_signal_seconds):
                signal_rollup["signals_rejected"] = int(signal_rollup.get("signals_rejected", 0)) + 1
                by_reason = signal_rollup.setdefault("signal_rejected_by_reason", {})
                by_reason["stale_signal"] = int(by_reason.get("stale_signal", 0)) + 1
                continue

        signal_rollup["signals_accepted"] = int(signal_rollup.get("signals_accepted", 0)) + 1
        candidates.append(
            {
                "token_address": str(sig.token_address),
                "symbol": str(sig.symbol),
                "entry_price": float(sig.entry_price),
                "usd_size": float(sig.usd_size),
                "metadata": md,
            }
        )

    out = run_live_pilot_auto_window_candidates(
        candidates=candidates,
        window_seconds=window_seconds,
        max_auto_trades=max_auto_trades,
        poll_interval_seconds=poll_interval_seconds,
        stop_on_reconciliation_mismatch=stop_on_reconciliation_mismatch,
        stop_on_reconciliation_inconclusive=stop_on_reconciliation_inconclusive,
        audit_log_dir=audit_log_dir,
        adapter_config=adapter_config,
        rpc_client=rpc_client,
        dex_executor=dex_executor,
        rpc_transport=rpc_transport,
        dex_quote_transport=dex_quote_transport,
        dex_swap_transport=dex_swap_transport,
        mechanical_safety_filter=mechanical_safety_filter,
        volatility_guard=volatility_guard,
        now_fn=now_fn,
        sleep_fn=sleep_fn,
    )
    out_rollup = out.setdefault("rollup", {})
    out_rollup["signals_seen"] = signal_rollup["signals_seen"]
    out_rollup["signals_accepted"] = signal_rollup["signals_accepted"]
    out_rollup["signals_rejected"] = signal_rollup["signals_rejected"]
    out_rollup["signal_rejected_by_reason"] = dict(signal_rollup.get("signal_rejected_by_reason", {}))
    out_rollup["signal_provider_metrics"] = dict(signal_rollup.get("provider_metrics", {}))
    out["promotion_gate_summary"] = _evaluate_live_pilot_promotion_gates(out_rollup, (adapter_config or {}).get("live_pilot_promotion_gates"))
    audit_log_path = str(out.get("audit_log_path") or "")
    if audit_log_path:
        promotion_gate_summary = dict(out.get("promotion_gate_summary") or {})
        append_audit_event(
            audit_log_path,
            "live_pilot_service_signal_provider_summary",
            {
                "signals_seen": out_rollup.get("signals_seen", 0),
                "signals_accepted": out_rollup.get("signals_accepted", 0),
                "signals_rejected": out_rollup.get("signals_rejected", 0),
                "signal_rejected_by_reason": dict(out_rollup.get("signal_rejected_by_reason", {})),
                "signal_provider_metrics": dict(out_rollup.get("signal_provider_metrics", {})),
            },
        )
        # Re-emit completed event as the final line so audit readers using the latest completed event
        # see the enriched rollup for signal-provider runs.
        append_audit_event(
            audit_log_path,
            "live_pilot_promotion_gate_evaluation",
            {"promotion_gate_summary": dict(promotion_gate_summary)},
        )
        append_audit_event(
            audit_log_path,
            "live_pilot_service_completed",
            {
                "rollup": dict(out_rollup),
                "live_pilot_summary": dict((out or {}).get("live_pilot_summary") or {}),
                "promotion_gate_summary": dict((out or {}).get("promotion_gate_summary") or {}),
                "completion_stage": "signal_provider_enriched",
            },
        )
    return out


def _main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--token-address", required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--entry-price", type=float, required=True)
    p.add_argument("--usd-size", type=float, required=True)
    p.add_argument("--mode", type=str, default="")
    p.add_argument("--audit-log-dir", default="data/exports")
    p.add_argument("--iterations", type=int, default=1)
    p.add_argument("--auto-pilot-window-seconds", type=float, default=0.0)
    p.add_argument("--auto-pilot-max-trades", type=int, default=1)
    p.add_argument("--auto-pilot-poll-interval-seconds", type=float, default=0.0)
    p.add_argument("--auto-pilot-stop-on-reconciliation-mismatch", action="store_true")
    p.add_argument("--auto-pilot-stop-on-reconciliation-inconclusive", action="store_true")
    p.add_argument("--enable-live-auto-submit-window", action="store_true")
    p.add_argument("--preflight-only", action="store_true")
    p.add_argument("--print-human-summary", action="store_true")
    p.add_argument("--campaign-runs", type=int, default=0)
    p.add_argument("--campaign-id", type=str, default="")
    p.add_argument("--campaign-state-json-path", default="")
    p.add_argument("--campaign-report-path", default="")
    p.add_argument("--resume-campaign", action="store_true")
    p.add_argument("--alerts-jsonl-path", default="")
    p.add_argument("--alert-console", action="store_true")
    p.add_argument("--alert-webhook-url", default="")
    p.add_argument("--alert-on-promotion-gate-fail", action="store_true")
    p.add_argument("--alert-on-campaign-stop", action="store_true")
    p.add_argument("--candidate-list-json", default="")
    p.add_argument("--candidate-list-json-path", default="")
    p.add_argument("--signal-provider-json-path", default="")
    p.add_argument("--use-dexscreener-signals", action="store_true")
    p.add_argument("--dexscreener-fetch-url", type=str, default="")
    p.add_argument("--dexscreener-fallback-urls-json-path", type=str, default="")
    p.add_argument("--dexscreener-fetch-timeout-seconds", type=float, default=5.0)
    p.add_argument("--dexscreener-fetch-max-attempts", type=int, default=1)
    p.add_argument("--dexscreener-fetch-retry-backoff-seconds", type=float, default=0.0)
    p.add_argument("--dexscreener-max-payload-age-ms", type=int, default=None)
    p.add_argument("--dexscreener-allow-stale-payloads", action="store_true")
    p.add_argument("--dexscreener-user-agent", type=str, default="")
    p.add_argument("--dexscreener-header", action="append", default=[])
    p.add_argument("--dexscreener-chain-id", type=str, default="solana")
    p.add_argument("--dexscreener-min-liquidity-usd", type=float, default=None)
    p.add_argument("--dexscreener-max-pair-age-seconds", type=float, default=None)
    p.add_argument("--signal-require-fresh-seconds", type=float, default=0.0)
    p.add_argument("--signal-max-candidates-per-window", type=int, default=10)
    p.add_argument("--allow-unsafe-paths", action="store_true")
    p.add_argument("--adapter-config-json", default="")
    p.add_argument("--adapter-config-json-path", default="")
    args = p.parse_args()
    _apply_live_pilot_mode_preset(args)

    if not args.allow_unsafe_paths:
        ensure_dir_within_base(args.audit_log_dir)
        if args.campaign_state_json_path:
            ensure_dir_within_base(str(Path(args.campaign_state_json_path).parent))
        if args.campaign_report_path:
            ensure_dir_within_base(str(Path(args.campaign_report_path).parent))
        if args.alerts_jsonl_path:
            ensure_dir_within_base(str(Path(args.alerts_jsonl_path).parent))

    adapter_config = None
    if args.adapter_config_json and args.adapter_config_json_path:
        raise ValueError("provide only one of --adapter-config-json or --adapter-config-json-path")
    if args.adapter_config_json_path:
        adapter_config = json.loads(Path(args.adapter_config_json_path).read_text(encoding="utf-8"))
    elif args.adapter_config_json:
        adapter_config = json.loads(args.adapter_config_json)
    preflight = _build_live_pilot_preflight(args, adapter_config=adapter_config)
    if args.preflight_only:
        print(json.dumps({"preflight": preflight}, sort_keys=True))
        return 0 if preflight.get("ready", False) else 2
    if args.candidate_list_json and args.candidate_list_json_path:
        raise ValueError("provide only one of --candidate-list-json or --candidate-list-json-path")
    if args.signal_provider_json_path and (args.candidate_list_json or args.candidate_list_json_path):
        raise ValueError("provide signal provider input OR candidate list input, not both")
    candidate_list = None
    if args.candidate_list_json_path:
        candidate_list = json.loads(Path(args.candidate_list_json_path).read_text(encoding="utf-8"))
    elif args.candidate_list_json:
        candidate_list = json.loads(args.candidate_list_json)
    def _execute_single_run():
        signal_provider = _build_live_pilot_signal_provider_from_args(args)
        if float(args.auto_pilot_window_seconds or 0.0) > 0:
            _validate_live_auto_window_guardrails(
                adapter_config=adapter_config,
                max_auto_trades=int(args.auto_pilot_max_trades),
                explicit_live_auto_submit_enable=bool(args.enable_live_auto_submit_window),
            )
            if signal_provider is not None:
                mechanical_safety_filter = _build_live_pilot_mechanical_safety_filter_from_config(adapter_config or {})
                volatility_guard = _build_live_pilot_volatility_guard_from_config(adapter_config or {})
                return run_live_pilot_auto_window_from_signal_provider(
                    signal_provider=signal_provider,
                    window_seconds=float(args.auto_pilot_window_seconds),
                    max_auto_trades=int(args.auto_pilot_max_trades),
                    max_candidates_per_window=int(args.signal_max_candidates_per_window),
                    poll_interval_seconds=float(args.auto_pilot_poll_interval_seconds),
                    require_fresh_signal_seconds=(None if float(args.signal_require_fresh_seconds or 0.0) <= 0 else float(args.signal_require_fresh_seconds)),
                    stop_on_reconciliation_mismatch=bool(args.auto_pilot_stop_on_reconciliation_mismatch),
                    stop_on_reconciliation_inconclusive=bool(args.auto_pilot_stop_on_reconciliation_inconclusive),
                    audit_log_dir=args.audit_log_dir,
                    adapter_config=adapter_config,
                    mechanical_safety_filter=mechanical_safety_filter,
                    volatility_guard=volatility_guard,
                )
            if isinstance(candidate_list, list):
                mechanical_safety_filter = _build_live_pilot_mechanical_safety_filter_from_config(adapter_config or {})
                volatility_guard = _build_live_pilot_volatility_guard_from_config(adapter_config or {})
                return run_live_pilot_auto_window_candidates(
                    candidates=candidate_list,
                    window_seconds=float(args.auto_pilot_window_seconds),
                    max_auto_trades=int(args.auto_pilot_max_trades),
                    poll_interval_seconds=float(args.auto_pilot_poll_interval_seconds),
                    stop_on_reconciliation_mismatch=bool(args.auto_pilot_stop_on_reconciliation_mismatch),
                    stop_on_reconciliation_inconclusive=bool(args.auto_pilot_stop_on_reconciliation_inconclusive),
                    audit_log_dir=args.audit_log_dir,
                    adapter_config=adapter_config,
                    mechanical_safety_filter=mechanical_safety_filter,
                    volatility_guard=volatility_guard,
                )
            return run_live_pilot_auto_window(
                token_address=args.token_address,
                symbol=args.symbol,
                entry_price=args.entry_price,
                usd_size=args.usd_size,
                window_seconds=float(args.auto_pilot_window_seconds),
                max_auto_trades=int(args.auto_pilot_max_trades),
                poll_interval_seconds=float(args.auto_pilot_poll_interval_seconds),
                stop_on_reconciliation_mismatch=bool(args.auto_pilot_stop_on_reconciliation_mismatch),
                stop_on_reconciliation_inconclusive=bool(args.auto_pilot_stop_on_reconciliation_inconclusive),
                audit_log_dir=args.audit_log_dir,
                adapter_config=adapter_config,
            )
        if int(args.iterations) == 1:
            return run_live_pilot_service_once(
                token_address=args.token_address,
                symbol=args.symbol,
                entry_price=args.entry_price,
                usd_size=args.usd_size,
                audit_log_dir=args.audit_log_dir,
                adapter_config=adapter_config,
            )
        return run_live_pilot_service_loop(
            token_address=args.token_address,
            symbol=args.symbol,
            entry_price=args.entry_price,
            usd_size=args.usd_size,
            iterations=args.iterations,
            audit_log_dir=args.audit_log_dir,
            adapter_config=adapter_config,
        )

    if int(args.campaign_runs or 0) > 0:
        resolved_campaign_id = str(args.campaign_id or f"pilot_campaign_{int(time.time())}")
        campaign_alert_emitter = None
        if str(args.alerts_jsonl_path or "").strip() or bool(args.alert_console) or str(args.alert_webhook_url or "").strip():
            campaign_alert_emitter = _build_campaign_alert_emitter(
                campaign_id=resolved_campaign_id,
                alerts_jsonl_path=str(args.alerts_jsonl_path or ""),
                console=bool(args.alert_console),
                webhook_url=str(args.alert_webhook_url or ""),
            )
        campaign = run_live_pilot_campaign(
            campaign_runs=int(args.campaign_runs),
            run_once_fn=_execute_single_run,
            campaign_id=resolved_campaign_id,
            campaign_state_json_path=str(args.campaign_state_json_path or ""),
            campaign_report_path=str(args.campaign_report_path or ""),
            resume_campaign=bool(args.resume_campaign),
            promotion_gate_config=((adapter_config or {}).get("live_pilot_promotion_gates") if isinstance(adapter_config, dict) else None),
            alert_emitter=campaign_alert_emitter,
            alert_policy=((adapter_config or {}).get("live_pilot_campaign_alerts") if isinstance(adapter_config, dict) else None),
            alert_on_promotion_gate_fail=bool(args.alert_on_promotion_gate_fail),
            alert_on_campaign_stop=bool(args.alert_on_campaign_stop),
        )
        cli_out = {
            "campaign_summary": campaign.get("campaign_summary"),
            "report_path": campaign.get("report_path", ""),
            "state_path": campaign.get("state_path", ""),
        }
        print(json.dumps(cli_out, sort_keys=True))
        if bool(args.print_human_summary):
            aggregate = dict(((campaign.get("campaign_summary") or {}).get("aggregate_rollup") or {}))
            human_in = {
                "rollup": aggregate,
                "live_pilot_summary": dict((campaign.get("runs") or [{}])[-1].get("live_pilot_summary") if campaign.get("runs") else {}),
                "promotion_gate_summary": dict(((campaign.get("campaign_summary") or {}).get("promotion_gate_summary") or {})),
            }
            print(_format_human_live_pilot_summary(human_in))
        return 0

    out = _execute_single_run()
    cli_out = {
        "audit_log_path": out["audit_log_path"],
        "rollup": out["rollup"],
        "live_pilot_summary": out.get("live_pilot_summary"),
        "promotion_gate_summary": out.get("promotion_gate_summary"),
    }
    print(json.dumps(cli_out, sort_keys=True))
    if bool(args.print_human_summary):
        print(_format_human_live_pilot_summary(cli_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
