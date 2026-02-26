import argparse
import hashlib
import json
import shutil
import sys
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
        "max_adaptive_candidate_quarantined_count": int(cfg.get("max_adaptive_candidate_quarantined_count", 999999)),
        "max_adaptive_fallback_quality_degraded_events": int(cfg.get("max_adaptive_fallback_quality_degraded_events", 999999)),
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
        "adaptive_candidate_quarantined_count": int(rollup.get("adaptive_candidate_quarantined_count", 0) or 0),
        "adaptive_fallback_quality_degraded_events": int(rollup.get("adaptive_fallback_quality_degraded_events", 0) or 0),
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
        (
            "max_adaptive_candidate_quarantined_count",
            metrics["adaptive_candidate_quarantined_count"] <= thresholds["max_adaptive_candidate_quarantined_count"],
            metrics["adaptive_candidate_quarantined_count"],
            thresholds["max_adaptive_candidate_quarantined_count"],
            "<=",
        ),
        (
            "max_adaptive_fallback_quality_degraded_events",
            metrics["adaptive_fallback_quality_degraded_events"] <= thresholds["max_adaptive_fallback_quality_degraded_events"],
            metrics["adaptive_fallback_quality_degraded_events"],
            thresholds["max_adaptive_fallback_quality_degraded_events"],
            "<=",
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


def _build_campaign_adaptive_gate_rollup(
    aggregate_rollup: dict[str, Any],
    campaign_extra_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = dict(aggregate_rollup or {})
    extra = dict(campaign_extra_summary or {})
    adaptive_fallback = dict(extra.get("adaptive_fallback_candidate_summary") or {})
    if adaptive_fallback:
        out["adaptive_candidate_quarantined_count"] = int(adaptive_fallback.get("adaptive_candidate_quarantined_count", 0) or 0)
    fallback_probe = dict(extra.get("fallback_candidate_probe_summary") or {})
    if fallback_probe:
        ok_n = int(fallback_probe.get("fallback_candidates_probe_ok", 0) or 0)
        fail_n = int(fallback_probe.get("fallback_candidates_probe_failed", 0) or 0)
        out["adaptive_fallback_quality_degraded_events"] = 1 if (ok_n < fail_n and (ok_n + fail_n) > 0) else 0
    return out


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


def _summarize_campaign_discovery_providers(run_records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "provider_usage_by_provider": {},
        "provider_failover_count": 0,
        "active_provider_by_run": [],
        "per_provider_metrics": {},
    }
    for rec in list(run_records or []):
        if not isinstance(rec, dict):
            continue
        cp = dict(rec.get("campaign_provider") or {})
        provider = str(cp.get("executed_provider") or cp.get("provider") or "")
        if not provider:
            continue
        summary["active_provider_by_run"].append({"run_index": rec.get("run_index"), "provider": provider})
        usage = summary.setdefault("provider_usage_by_provider", {})
        usage[provider] = int(usage.get(provider, 0)) + 1
        if bool(cp.get("failover_applied", False)):
            summary["provider_failover_count"] = int(summary.get("provider_failover_count", 0)) + 1

        ppm = summary.setdefault("per_provider_metrics", {}).setdefault(
            provider,
            {
                "runs": 0,
                "signals_seen": 0,
                "signals_accepted": 0,
                "signals_rejected": 0,
                "candidates_seen": 0,
                "candidates_attempted": 0,
                "candidates_submitted": 0,
                "fetch_transport_errors": 0,
                "fetch_endpoint_failure_events": 0,
            },
        )
        ppm["runs"] = int(ppm.get("runs", 0)) + 1
        rr = dict(rec.get("rollup") or {})
        for key in ("signals_seen", "signals_accepted", "signals_rejected", "candidates_seen", "candidates_attempted", "candidates_submitted"):
            ppm[key] = int(ppm.get(key, 0)) + int(rr.get(key, 0) or 0)
        spm = dict(rr.get("signal_provider_metrics") or {})
        ppm["fetch_transport_errors"] = int(ppm.get("fetch_transport_errors", 0)) + int(spm.get("fetch_transport_errors", 0) or 0)
        ppm["fetch_endpoint_failure_events"] = int(ppm.get("fetch_endpoint_failure_events", 0)) + int(
            spm.get("fetch_endpoint_failure_events", 0) or 0
        )
    return summary


def sanitize_fallback_candidates(candidates: list[Any]) -> dict[str, Any]:
    if not isinstance(candidates, list):
        raise ValueError("fallback candidates must be a list")
    sanitized: list[dict[str, Any]] = []
    dropped_by_reason: dict[str, int] = {}
    for row in candidates:
        if not isinstance(row, dict):
            dropped_by_reason["invalid_type"] = int(dropped_by_reason.get("invalid_type", 0)) + 1
            continue
        token_address = str(row.get("token_address") or "").strip()
        if not token_address:
            dropped_by_reason["missing_token_address"] = int(dropped_by_reason.get("missing_token_address", 0)) + 1
            continue
        symbol = str(row.get("symbol") or "").strip() or token_address[:6] or "UNK"
        entry_price = _to_float_or_none(row.get("entry_price"))
        if entry_price is None or entry_price <= 0:
            entry_price = 1.0
        usd_size = _to_float_or_none(row.get("usd_size"))
        if usd_size is None or usd_size <= 0:
            usd_size = 1.0
        sanitized.append(
            {
                "token_address": token_address,
                "symbol": symbol,
                "entry_price": float(entry_price),
                "usd_size": float(usd_size),
                "metadata": dict(row.get("metadata") or {}),
            }
        )
    return {
        "candidates": sanitized,
        "summary": {
            "fallback_candidates_total": len(candidates),
            "fallback_candidates_sanitized_count": len(sanitized),
            "fallback_candidates_dropped_count": int(sum(dropped_by_reason.values())),
            "fallback_candidates_dropped_by_reason": dropped_by_reason,
        },
    }


def probe_fallback_candidates_preflight(
    candidates: list[dict[str, Any]],
    *,
    probe_count: int,
    adapter_config: dict[str, Any] | None = None,
    quote_probe_fail_closed: bool = False,
    quote_probe_min_pass_rate: float = 0.5,
    quote_probe_warn_failure_rate: float = 0.5,
    probe_fn=None,
) -> dict[str, Any]:
    candidates = list(candidates or [])
    probe_count = max(0, int(probe_count))
    if probe_count <= 0 or not candidates:
        return {
            "candidates": candidates,
            "summary": {
                "fallback_candidates_total": len(candidates),
                "fallback_candidates_probe_count": 0,
                "fallback_candidates_probe_ok": 0,
                "fallback_candidates_probe_failed": 0,
                "fallback_candidates_probe_skipped": len(candidates),
                "fallback_candidates_probe_pass_rate": None,
                "fallback_candidates_probe_fail_closed_triggered": False,
                "fallback_candidates_probe_fail_closed_enabled": bool(quote_probe_fail_closed),
                "fallback_candidates_probe_min_pass_rate": float(quote_probe_min_pass_rate),
                "fallback_candidates_probe_warn_failure_rate": float(quote_probe_warn_failure_rate),
                "fallback_candidates_probe_failures": [],
            },
        }

    if probe_fn is None:
        cfg = dict(adapter_config or {})
        cfg["live_send_network_enabled"] = False
        cfg.setdefault("audit_log_path", "data/exports/fallback_candidate_probe_preflight.jsonl")
        adapter = LiveExecutionAdapter(cfg)

        def probe_fn(candidate: dict[str, Any]) -> dict[str, Any]:
            try:
                adapter.buy(
                    str(candidate.get("token_address")),
                    str(candidate.get("symbol")),
                    float(candidate.get("entry_price", 1.0) or 1.0),
                    float(candidate.get("usd_size", 1.0) or 1.0),
                )
                return {"ok": True, "reason": ""}
            except Exception as exc:
                return {"ok": False, "reason": str(exc)}

    kept: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    successes: list[dict[str, Any]] = []
    probe_ok = 0
    probe_failed = 0
    for idx, cand in enumerate(candidates):
        if idx >= probe_count:
            kept.append(cand)
            continue
        res = probe_fn(cand)
        if bool((res or {}).get("ok", False)):
            probe_ok += 1
            kept.append(cand)
            successes.append(
                {
                    "candidate_index": idx,
                    "token_address": str(cand.get("token_address") or ""),
                    "symbol": str(cand.get("symbol") or ""),
                }
            )
        else:
            probe_failed += 1
            failures.append(
                {
                    "candidate_index": idx,
                    "token_address": str(cand.get("token_address") or ""),
                    "symbol": str(cand.get("symbol") or ""),
                    "reason": str((res or {}).get("reason") or "probe_failed"),
                }
            )
    probed = min(len(candidates), probe_count)
    pass_rate = (float(probe_ok) / float(probed)) if probed > 0 else None
    fail_closed_triggered = bool(quote_probe_fail_closed and pass_rate is not None and pass_rate < float(quote_probe_min_pass_rate))
    return {
        "candidates": kept,
        "summary": {
            "fallback_candidates_total": len(candidates),
            "fallback_candidates_probe_count": probed,
            "fallback_candidates_probe_ok": probe_ok,
            "fallback_candidates_probe_failed": probe_failed,
            "fallback_candidates_probe_skipped": max(0, len(candidates) - probed),
            "fallback_candidates_probe_pass_rate": (None if pass_rate is None else round(pass_rate, 6)),
            "fallback_candidates_probe_fail_closed_triggered": fail_closed_triggered,
            "fallback_candidates_probe_fail_closed_enabled": bool(quote_probe_fail_closed),
            "fallback_candidates_probe_min_pass_rate": float(quote_probe_min_pass_rate),
            "fallback_candidates_probe_warn_failure_rate": float(quote_probe_warn_failure_rate),
            "fallback_candidates_probe_failures": failures,
            "fallback_candidates_probe_successes": successes,
        },
    }


def load_adaptive_reliability_state(path_str: str) -> dict[str, Any]:
    if not str(path_str or "").strip():
        return {"candidate_stats": {}, "provider_stats": {}, "meta": {}}
    p = Path(path_str)
    if not p.exists():
        return {"candidate_stats": {}, "provider_stats": {}, "meta": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"candidate_stats": {}, "provider_stats": {}, "meta": {"load_error": True}}
    if not isinstance(data, dict):
        return {"candidate_stats": {}, "provider_stats": {}, "meta": {"load_error": True}}
    data.setdefault("candidate_stats", {})
    data.setdefault("provider_stats", {})
    data.setdefault("meta", {})
    return data


def save_adaptive_reliability_state(path_str: str, state: dict[str, Any]) -> None:
    if not str(path_str or "").strip():
        return
    p = Path(path_str)
    p.write_text(json.dumps(dict(state or {}), sort_keys=True, indent=2), encoding="utf-8")


def _decay_adaptive_reliability_state(
    state: dict[str, Any],
    *,
    candidate_decay_factor: float = 0.9,
    provider_decay_factor: float = 0.95,
) -> dict[str, Any]:
    out = dict(state or {})
    cstats = dict(out.get("candidate_stats") or {})
    pstats = dict(out.get("provider_stats") or {})
    cdf = max(0.0, min(1.0, float(candidate_decay_factor)))
    pdf = max(0.0, min(1.0, float(provider_decay_factor)))
    candidate_numeric_keys = {"probe_ok", "probe_failed", "execution_error", "submitted"}
    provider_numeric_keys = {"runs", "transport_errors", "endpoint_failures", "hard_stops", "successful_runs"}
    for tok, row in list(cstats.items()):
        if not isinstance(row, dict):
            continue
        nr = dict(row)
        for k in candidate_numeric_keys:
            if k in nr:
                nr[k] = round(float(nr.get(k, 0) or 0) * cdf, 6)
        cstats[str(tok)] = nr
    for prov, row in list(pstats.items()):
        if not isinstance(row, dict):
            continue
        nr = dict(row)
        for k in provider_numeric_keys:
            if k in nr:
                nr[k] = round(float(nr.get(k, 0) or 0) * pdf, 6)
        pstats[str(prov)] = nr
    out["candidate_stats"] = cstats
    out["provider_stats"] = pstats
    meta = dict(out.get("meta") or {})
    meta["last_decay_applied_unix_ms"] = int(time.time() * 1000)
    meta["candidate_decay_factor"] = cdf
    meta["provider_decay_factor"] = pdf
    out["meta"] = meta
    return out


def _candidate_quality_score(
    candidate: dict[str, Any],
    state: dict[str, Any],
    *,
    quarantine_failure_threshold: int = 3,
    quarantine_ttl_seconds: float | None = None,
) -> dict[str, Any]:
    tok = str((candidate or {}).get("token_address") or "")
    cs = dict(((state or {}).get("candidate_stats") or {}).get(tok) or {})
    md = dict((candidate or {}).get("metadata") or {})
    confidence_hint = _to_float_or_none(md.get("confidence_hint")) or 0.0
    probe_ok = float(cs.get("probe_ok", 0) or 0)
    probe_failed = float(cs.get("probe_failed", 0) or 0)
    exec_errors = float(cs.get("execution_error", 0) or 0)
    submitted = float(cs.get("submitted", 0) or 0)
    score = round(confidence_hint + (probe_ok * 2.0) + (submitted * 1.0) - (probe_failed * 3.0) - (exec_errors * 4.0), 6)
    last_negative_unix_ms = _to_int_or_none(cs.get("last_negative_unix_ms"))
    quarantine_age_seconds = None
    quarantine_ttl_expired = False
    if last_negative_unix_ms is not None:
        quarantine_age_seconds = max(0.0, (time.time() * 1000.0 - float(last_negative_unix_ms)) / 1000.0)
        if quarantine_ttl_seconds is not None and quarantine_age_seconds > float(quarantine_ttl_seconds):
            quarantine_ttl_expired = True
    quarantined = ((probe_failed + exec_errors) >= int(quarantine_failure_threshold)) and not quarantine_ttl_expired
    return {
        "token_address": tok,
        "symbol": str((candidate or {}).get("symbol") or ""),
        "score": score,
        "probe_ok": probe_ok,
        "probe_failed": probe_failed,
        "execution_error": exec_errors,
        "submitted": submitted,
        "confidence_hint": confidence_hint,
        "quarantined": quarantined,
        "quarantine_ttl_expired": quarantine_ttl_expired,
        "quarantine_age_seconds": (None if quarantine_age_seconds is None else round(quarantine_age_seconds, 6)),
        "last_negative_unix_ms": last_negative_unix_ms,
    }


def adaptive_reorder_fallback_candidates(
    candidates: list[dict[str, Any]],
    *,
    reliability_state: dict[str, Any] | None = None,
    enabled: bool = False,
    quarantine_failure_threshold: int = 3,
    quarantine_ttl_seconds: float | None = None,
) -> dict[str, Any]:
    candidates = list(candidates or [])
    if not enabled or not candidates:
        return {
            "candidates": candidates,
            "summary": {
                "adaptive_candidate_reordering_applied": False,
                "adaptive_candidate_reordering_enabled": bool(enabled),
                "adaptive_candidate_quarantined_count": 0,
                "adaptive_candidate_ttl_expired_count": 0,
                "adaptive_candidate_top_scores": [],
                "adaptive_candidate_bottom_scores": [],
            },
        }
    scored = []
    for c in candidates:
        scored.append(
            {
                "candidate": dict(c or {}),
                "scorecard": _candidate_quality_score(
                    c,
                    reliability_state or {},
                    quarantine_failure_threshold=quarantine_failure_threshold,
                    quarantine_ttl_seconds=quarantine_ttl_seconds,
                ),
            }
        )
    kept = [x for x in scored if not bool((x.get("scorecard") or {}).get("quarantined", False))]
    quarantined = [x for x in scored if bool((x.get("scorecard") or {}).get("quarantined", False))]
    ttl_expired = [x for x in scored if bool((x.get("scorecard") or {}).get("quarantine_ttl_expired", False))]
    kept_sorted = sorted(kept, key=lambda x: (-(x["scorecard"]["score"]), str(x["scorecard"]["token_address"])))
    out_candidates = [x["candidate"] for x in kept_sorted]
    top_scores = [dict(x["scorecard"]) for x in kept_sorted[:5]]
    bottom_pool = sorted(kept, key=lambda x: (x["scorecard"]["score"], str(x["scorecard"]["token_address"])))
    bottom_scores = [dict(x["scorecard"]) for x in bottom_pool[:5]]
    return {
        "candidates": out_candidates,
        "summary": {
            "adaptive_candidate_reordering_applied": True,
            "adaptive_candidate_reordering_enabled": True,
            "adaptive_candidate_quarantined_count": len(quarantined),
            "adaptive_candidate_ttl_expired_count": len(ttl_expired),
            "adaptive_candidate_quarantine_threshold": int(quarantine_failure_threshold),
            "adaptive_candidate_quarantine_ttl_seconds": (None if quarantine_ttl_seconds is None else float(quarantine_ttl_seconds)),
            "adaptive_candidate_top_scores": top_scores,
            "adaptive_candidate_bottom_scores": bottom_scores,
            "adaptive_candidate_quarantined_tokens": [x["scorecard"]["token_address"] for x in quarantined],
        },
    }


def adaptive_reorder_provider_order(
    provider_order: list[str],
    *,
    reliability_state: dict[str, Any] | None = None,
    enabled: bool = False,
) -> dict[str, Any]:
    provider_order = [str(x).strip().lower() for x in list(provider_order or []) if str(x).strip()]
    if not enabled or len(provider_order) <= 1:
        return {
            "provider_order": provider_order,
            "summary": {
                "adaptive_provider_order_applied": False,
                "adaptive_provider_order_enabled": bool(enabled),
                "adaptive_provider_scores": {},
            },
        }
    ps = dict((reliability_state or {}).get("provider_stats") or {})
    def _score(p: str) -> float:
        st = dict(ps.get(p) or {})
        return round((int(st.get("successful_runs", 0) or 0) * 2.0) - (int(st.get("transport_errors", 0) or 0) * 3.0) - (int(st.get("hard_stops", 0) or 0) * 4.0), 6)
    scores = {p: _score(p) for p in provider_order}
    reordered = sorted(provider_order, key=lambda p: (-scores[p], provider_order.index(p)))
    return {
        "provider_order": reordered,
        "summary": {
            "adaptive_provider_order_applied": reordered != provider_order,
            "adaptive_provider_order_enabled": True,
            "adaptive_provider_order_before": provider_order,
            "adaptive_provider_order_after": reordered,
            "adaptive_provider_scores": scores,
        },
    }


def update_adaptive_reliability_state_from_campaign_report(state: dict[str, Any], campaign_report: dict[str, Any]) -> dict[str, Any]:
    meta_in = dict((state or {}).get("meta") or {})
    out = _decay_adaptive_reliability_state(
        dict(state or {}),
        candidate_decay_factor=float(meta_in.get("candidate_decay_factor", 0.9) or 0.9),
        provider_decay_factor=float(meta_in.get("provider_decay_factor", 0.95) or 0.95),
    )
    cstats = dict(out.get("candidate_stats") or {})
    pstats = dict(out.get("provider_stats") or {})
    summary = dict((campaign_report or {}).get("campaign_summary") or {})
    fb = dict(summary.get("fallback_candidate_probe_summary") or {})
    for row in list(fb.get("fallback_candidates_probe_successes") or []):
        if not isinstance(row, dict):
            continue
        tok = str(row.get("token_address") or "")
        if not tok:
            continue
        cs = dict(cstats.get(tok) or {})
        cs["probe_ok"] = round(float(cs.get("probe_ok", 0) or 0) + 1.0, 6)
        cs["last_seen_unix_ms"] = int(time.time() * 1000)
        cstats[tok] = cs
    for row in list(fb.get("fallback_candidates_probe_failures") or []):
        if not isinstance(row, dict):
            continue
        tok = str(row.get("token_address") or "")
        if not tok:
            continue
        cs = dict(cstats.get(tok) or {})
        cs["probe_failed"] = round(float(cs.get("probe_failed", 0) or 0) + 1.0, 6)
        cs["last_error"] = str(row.get("reason") or "")
        cs["last_negative_unix_ms"] = int(time.time() * 1000)
        cs["last_seen_unix_ms"] = int(time.time() * 1000)
        cstats[tok] = cs
    runs = list((campaign_report or {}).get("runs") or [])
    for rec in runs:
        if not isinstance(rec, dict):
            continue
        cp = dict(rec.get("campaign_provider") or {})
        provider = str(cp.get("executed_provider") or cp.get("provider") or "")
        if not provider:
            continue
        psr = dict(pstats.get(provider) or {})
        psr["runs"] = round(float(psr.get("runs", 0) or 0) + 1.0, 6)
        rr = dict(rec.get("rollup") or {})
        spm = dict(rr.get("signal_provider_metrics") or {})
        psr["transport_errors"] = round(float(psr.get("transport_errors", 0) or 0) + float(spm.get("fetch_transport_errors", 0) or 0), 6)
        psr["endpoint_failures"] = round(float(psr.get("endpoint_failures", 0) or 0) + float(spm.get("fetch_endpoint_failure_events", 0) or 0), 6)
        if bool(cp.get("execution_error", False)):
            psr["hard_stops"] = round(float(psr.get("hard_stops", 0) or 0) + 1.0, 6)
        else:
            psr["successful_runs"] = round(float(psr.get("successful_runs", 0) or 0) + 1.0, 6)
        psr["last_seen_unix_ms"] = int(time.time() * 1000)
        pstats[provider] = psr
    out["candidate_stats"] = cstats
    out["provider_stats"] = pstats
    meta = dict(out.get("meta") or {})
    meta["updated_at_unix_ms"] = int(time.time() * 1000)
    out["meta"] = meta
    return out


def _build_campaign_alert_emitter(
    *,
    campaign_id: str,
    alerts_jsonl_path: str = "",
    console: bool = False,
    webhook_url: str = "",
    quiet_hours_start_hour_utc: int | None = None,
    quiet_hours_end_hour_utc: int | None = None,
    allow_critical_during_quiet_hours: bool = True,
    escalation_levels: dict[str, str] | None = None,
):
    alerts_file = Path(alerts_jsonl_path) if str(alerts_jsonl_path or "").strip() else None
    escalation_levels = {str(k): str(v) for k, v in dict(escalation_levels or {}).items()}

    def _in_quiet_hours(now_unix_ms: int) -> bool:
        if quiet_hours_start_hour_utc is None or quiet_hours_end_hour_utc is None:
            return False
        h = int(time.gmtime(float(now_unix_ms) / 1000.0).tm_hour)
        start_h = int(quiet_hours_start_hour_utc) % 24
        end_h = int(quiet_hours_end_hour_utc) % 24
        if start_h == end_h:
            return True
        if start_h < end_h:
            return start_h <= h < end_h
        return (h >= start_h) or (h < end_h)

    def emit(alert: dict[str, Any]) -> None:
        now_unix_ms = int(time.time() * 1000)
        base_level = str((alert or {}).get("level") or "info").lower()
        effective_level = str(escalation_levels.get(base_level, base_level))
        in_quiet = _in_quiet_hours(now_unix_ms)
        suppress_console = bool(in_quiet and not (allow_critical_during_quiet_hours and effective_level == "critical"))
        row = {
            "ts_unix_ms": now_unix_ms,
            "event_type": "live_pilot_campaign_alert",
            "campaign_id": campaign_id,
            **dict(alert or {}),
        }
        row["level"] = effective_level
        row["base_level"] = base_level
        row["quiet_hours_active"] = in_quiet
        row["console_suppressed_by_quiet_hours"] = suppress_console
        if webhook_url:
            # Future-safe placeholder: surface config presence without performing network calls.
            row.setdefault("webhook_configured", True)
        if alerts_file:
            with alerts_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        if console and not suppress_console:
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
    discovery = dict(summary.get("discovery_provider_summary") or {})
    fallback_probe = dict(summary.get("fallback_candidate_probe_summary") or {})
    adaptive_fallback = dict(summary.get("adaptive_fallback_candidate_summary") or {})
    adaptive_provider = dict(summary.get("adaptive_provider_order_summary") or {})
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
    if discovery:
        lines.extend(
            [
                "",
                "## Discovery Providers",
                "",
                f"- provider_failover_count: `{discovery.get('provider_failover_count', 0)}`",
                f"- provider_usage_by_provider: `{json.dumps(discovery.get('provider_usage_by_provider', {}), sort_keys=True)}`",
            ]
        )
    if fallback_probe:
        lines.extend(
            [
                "",
                "## Fallback Candidate Probe",
                "",
                f"- fallback_candidates_total: `{fallback_probe.get('fallback_candidates_total', 0)}`",
                f"- fallback_candidates_sanitized_count: `{fallback_probe.get('fallback_candidates_sanitized_count', 0)}`",
                f"- fallback_candidates_probe_ok: `{fallback_probe.get('fallback_candidates_probe_ok', 0)}`",
                f"- fallback_candidates_probe_failed: `{fallback_probe.get('fallback_candidates_probe_failed', 0)}`",
                f"- fallback_candidates_probe_pass_rate: `{fallback_probe.get('fallback_candidates_probe_pass_rate')}`",
                f"- fallback_candidates_probe_fail_closed_triggered: `{bool(fallback_probe.get('fallback_candidates_probe_fail_closed_triggered', False))}`",
            ]
        )
    if adaptive_fallback:
        lines.extend(
            [
                "",
                "## Adaptive Candidate Ordering",
                "",
                f"- adaptive_candidate_reordering_applied: `{bool(adaptive_fallback.get('adaptive_candidate_reordering_applied', False))}`",
                f"- adaptive_candidate_quarantined_count: `{adaptive_fallback.get('adaptive_candidate_quarantined_count', 0)}`",
                f"- adaptive_candidate_ttl_expired_count: `{adaptive_fallback.get('adaptive_candidate_ttl_expired_count', 0)}`",
            ]
        )
    if adaptive_provider:
        lines.extend(
            [
                "",
                "## Adaptive Provider Ordering",
                "",
                f"- adaptive_provider_order_applied: `{bool(adaptive_provider.get('adaptive_provider_order_applied', False))}`",
                f"- adaptive_provider_order_before: `{json.dumps(adaptive_provider.get('adaptive_provider_order_before', []))}`",
                f"- adaptive_provider_order_after: `{json.dumps(adaptive_provider.get('adaptive_provider_order_after', []))}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _extract_campaign_report_summary(report: dict[str, Any]) -> dict[str, Any]:
    summary = dict((report or {}).get("campaign_summary") or {})
    agg = dict(summary.get("aggregate_rollup") or {})
    gate = dict(summary.get("promotion_gate_summary") or {})
    alerts = dict(summary.get("alert_summary") or {})
    discovery = dict(summary.get("discovery_provider_summary") or {})
    fallback_probe = dict(summary.get("fallback_candidate_probe_summary") or {})
    adaptive_fallback = dict(summary.get("adaptive_fallback_candidate_summary") or {})
    adaptive_provider = dict(summary.get("adaptive_provider_order_summary") or {})
    return {
        "campaign_id": str(summary.get("campaign_id") or ""),
        "completed_runs": int(summary.get("completed_runs", 0) or 0),
        "target_runs": int(summary.get("target_runs", 0) or 0),
        "stop_reason": str(summary.get("stop_reason") or ""),
        "live_finalized_count": int(agg.get("live_finalized_count", 0) or 0),
        "live_reconciliation_mismatch_count": int(agg.get("live_reconciliation_mismatch_count", 0) or 0),
        "economics_samples_count": int(agg.get("economics_samples_count", 0) or 0),
        "fee_lamports_total": int(agg.get("fee_lamports_total", 0) or 0),
        "avg_realized_slippage_bps": _to_float_or_none(agg.get("avg_realized_slippage_bps")),
        "worst_realized_slippage_bps": _to_float_or_none(agg.get("worst_realized_slippage_bps")),
        "dexscreener_transport_errors": int(((agg.get("signal_provider_metrics") or {}).get("fetch_transport_errors", 0) or 0)),
        "dexscreener_endpoint_failures": int(((agg.get("signal_provider_metrics") or {}).get("fetch_endpoint_failure_events", 0) or 0)),
        "promotion_gate_status": str(gate.get("status") or ""),
        "promotion_gate_ready": bool(gate.get("ready_to_promote", False)),
        "promotion_gate_failed_checks": list(gate.get("failed_checks", []) or []),
        "alert_count": int(alerts.get("count", 0) or 0),
        "alert_levels": dict(alerts.get("by_level", {}) or {}),
        "provider_failover_count": int(discovery.get("provider_failover_count", 0) or 0),
        "provider_usage_by_provider": dict(discovery.get("provider_usage_by_provider", {}) or {}),
        "discovery_per_provider_metrics": dict(discovery.get("per_provider_metrics", {}) or {}),
        "fallback_candidates_total": int(fallback_probe.get("fallback_candidates_total", 0) or 0),
        "fallback_candidates_probe_ok": int(fallback_probe.get("fallback_candidates_probe_ok", 0) or 0),
        "fallback_candidates_probe_failed": int(fallback_probe.get("fallback_candidates_probe_failed", 0) or 0),
        "adaptive_candidate_quarantined_count": int(adaptive_fallback.get("adaptive_candidate_quarantined_count", 0) or 0),
        "adaptive_candidate_ttl_expired_count": int(adaptive_fallback.get("adaptive_candidate_ttl_expired_count", 0) or 0),
        "adaptive_candidate_reordering_applied": bool(adaptive_fallback.get("adaptive_candidate_reordering_applied", False)),
        "adaptive_provider_order_applied": bool(adaptive_provider.get("adaptive_provider_order_applied", False)),
        "adaptive_fallback_quality_degraded": bool(
            int(fallback_probe.get("fallback_candidates_probe_ok", 0) or 0)
            < int(fallback_probe.get("fallback_candidates_probe_failed", 0) or 0)
            and int(fallback_probe.get("fallback_candidates_probe_ok", 0) or 0) + int(fallback_probe.get("fallback_candidates_probe_failed", 0) or 0) > 0
        ),
    }


def aggregate_live_pilot_campaign_reports(
    reports: list[dict[str, Any]],
    *,
    recommendation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summaries = [_extract_campaign_report_summary(r) for r in list(reports or []) if isinstance(r, dict)]
    count = len(summaries)
    if count == 0:
        return {
            "campaign_count": 0,
            "campaigns": [],
            "aggregate": {},
            "trends": {},
            "recommendation": {"action": "hold", "confidence": "low", "reasons": ["no_campaign_reports"]},
        }
    aggregate = {
        "campaign_count": count,
        "completed_runs_total": sum(int(s["completed_runs"]) for s in summaries),
        "live_finalized_count_total": sum(int(s["live_finalized_count"]) for s in summaries),
        "live_reconciliation_mismatch_count_total": sum(int(s["live_reconciliation_mismatch_count"]) for s in summaries),
        "economics_samples_count_total": sum(int(s["economics_samples_count"]) for s in summaries),
        "fee_lamports_total": sum(int(s["fee_lamports_total"]) for s in summaries),
        "dexscreener_transport_errors_total": sum(int(s["dexscreener_transport_errors"]) for s in summaries),
        "dexscreener_endpoint_failures_total": sum(int(s["dexscreener_endpoint_failures"]) for s in summaries),
        "promotion_gate_pass_count": sum(1 for s in summaries if s["promotion_gate_status"] == "pass"),
        "promotion_gate_fail_count": sum(1 for s in summaries if s["promotion_gate_status"] == "fail"),
        "provider_failover_count_total": sum(int(s.get("provider_failover_count", 0) or 0) for s in summaries),
        "fallback_candidates_total": sum(int(s.get("fallback_candidates_total", 0) or 0) for s in summaries),
        "fallback_candidates_probe_ok_total": sum(int(s.get("fallback_candidates_probe_ok", 0) or 0) for s in summaries),
        "fallback_candidates_probe_failed_total": sum(int(s.get("fallback_candidates_probe_failed", 0) or 0) for s in summaries),
        "adaptive_candidate_quarantined_count_total": sum(int(s.get("adaptive_candidate_quarantined_count", 0) or 0) for s in summaries),
        "adaptive_candidate_ttl_expired_count_total": sum(int(s.get("adaptive_candidate_ttl_expired_count", 0) or 0) for s in summaries),
        "adaptive_candidate_reordering_applied_count": sum(1 for s in summaries if bool(s.get("adaptive_candidate_reordering_applied", False))),
        "adaptive_provider_order_applied_count": sum(1 for s in summaries if bool(s.get("adaptive_provider_order_applied", False))),
        "adaptive_fallback_quality_degraded_count": sum(1 for s in summaries if bool(s.get("adaptive_fallback_quality_degraded", False))),
        "stop_reason_by_reason": {},
        "alert_level_by_level": {},
        "provider_usage_by_provider": {},
    }
    slippage_avgs = [float(s["avg_realized_slippage_bps"]) for s in summaries if s["avg_realized_slippage_bps"] is not None]
    slippage_worsts = [float(s["worst_realized_slippage_bps"]) for s in summaries if s["worst_realized_slippage_bps"] is not None]
    if slippage_avgs:
        aggregate["avg_realized_slippage_bps_across_campaigns"] = round(sum(slippage_avgs) / len(slippage_avgs), 6)
    if slippage_worsts:
        aggregate["worst_realized_slippage_bps_across_campaigns"] = round(max(slippage_worsts), 6)
    for s in summaries:
        sr = str(s.get("stop_reason") or "")
        if sr:
            m = aggregate.setdefault("stop_reason_by_reason", {})
            m[sr] = int(m.get(sr, 0)) + 1
        for lvl, n in dict(s.get("alert_levels") or {}).items():
            m = aggregate.setdefault("alert_level_by_level", {})
            m[str(lvl)] = int(m.get(str(lvl), 0)) + int(n or 0)
        for p, n in dict(s.get("provider_usage_by_provider") or {}).items():
            m = aggregate.setdefault("provider_usage_by_provider", {})
            m[str(p)] = int(m.get(str(p), 0)) + int(n or 0)

    trends = {
        "campaign_ids": [s["campaign_id"] for s in summaries],
        "promotion_gate_status_sequence": [s["promotion_gate_status"] for s in summaries],
        "finalized_count_sequence": [int(s["live_finalized_count"]) for s in summaries],
        "avg_slippage_bps_sequence": [s["avg_realized_slippage_bps"] for s in summaries],
        "provider_transport_errors_sequence": [int(s["dexscreener_transport_errors"]) for s in summaries],
        "provider_failover_count_sequence": [int(s.get("provider_failover_count", 0) or 0) for s in summaries],
        "provider_usage_sequence": [dict(s.get("provider_usage_by_provider", {}) or {}) for s in summaries],
        "fallback_probe_failed_sequence": [int(s.get("fallback_candidates_probe_failed", 0) or 0) for s in summaries],
        "adaptive_candidate_quarantined_count_sequence": [int(s.get("adaptive_candidate_quarantined_count", 0) or 0) for s in summaries],
        "adaptive_candidate_ttl_expired_count_sequence": [int(s.get("adaptive_candidate_ttl_expired_count", 0) or 0) for s in summaries],
        "stop_reason_sequence": [s["stop_reason"] for s in summaries],
    }
    if count >= 2:
        trends["finalized_count_delta_last"] = int(summaries[-1]["live_finalized_count"]) - int(summaries[-2]["live_finalized_count"])
        a_prev = summaries[-2]["avg_realized_slippage_bps"]
        a_last = summaries[-1]["avg_realized_slippage_bps"]
        trends["avg_slippage_bps_delta_last"] = None if a_prev is None or a_last is None else round(float(a_last) - float(a_prev), 6)
        trends["transport_error_delta_last"] = int(summaries[-1]["dexscreener_transport_errors"]) - int(summaries[-2]["dexscreener_transport_errors"])

    recommendation = _recommend_live_pilot_promotion_from_campaign_trends(
        {"campaigns": summaries, "aggregate": aggregate, "trends": trends},
        config=recommendation_config,
    )
    return {"campaign_count": count, "campaigns": summaries, "aggregate": aggregate, "trends": trends, "recommendation": recommendation}


def _recommend_live_pilot_promotion_from_campaign_trends(summary: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(config or {})
    campaigns = list(summary.get("campaigns") or [])
    aggregate = dict(summary.get("aggregate") or {})
    reasons: list[str] = []
    if not campaigns:
        return {"action": "hold", "confidence": "low", "reasons": ["no_campaigns"]}

    min_campaigns = int(cfg.get("min_campaigns", 3))
    min_total_finalized = int(cfg.get("min_total_finalized", 3))
    max_total_mismatches = int(cfg.get("max_total_reconciliation_mismatches", 0))
    max_total_transport_errors = int(cfg.get("max_total_dexscreener_transport_errors", 0))
    max_worst_slippage = float(cfg.get("max_worst_slippage_bps", 120.0))
    max_warning_alerts = int(cfg.get("max_warning_alerts", 9999))
    require_recent_gate_passes = int(cfg.get("require_recent_gate_passes", 2))
    allow_fallback_continue_on_provider_issues = bool(cfg.get("allow_fallback_continue_on_provider_issues", True))
    max_adaptive_quarantined_total = int(cfg.get("max_adaptive_quarantined_total", 999999))
    max_adaptive_fallback_quality_degraded_count = int(cfg.get("max_adaptive_fallback_quality_degraded_count", 999999))

    if len(campaigns) < min_campaigns:
        reasons.append("insufficient_campaign_count")
    if int(aggregate.get("live_finalized_count_total", 0)) < min_total_finalized:
        reasons.append("insufficient_total_finalized")
    if int(aggregate.get("live_reconciliation_mismatch_count_total", 0)) > max_total_mismatches:
        reasons.append("reconciliation_mismatch_total_exceeded")
    if int(aggregate.get("dexscreener_transport_errors_total", 0)) > max_total_transport_errors:
        reasons.append("provider_transport_errors_total_exceeded")
    worst = _to_float_or_none(aggregate.get("worst_realized_slippage_bps_across_campaigns"))
    if worst is None or worst > max_worst_slippage:
        reasons.append("worst_slippage_too_high_or_missing")
    warnings_total = int((aggregate.get("alert_level_by_level") or {}).get("warning", 0) or 0)
    if warnings_total > max_warning_alerts:
        reasons.append("warning_alert_volume_high")
    if int(aggregate.get("adaptive_candidate_quarantined_count_total", 0) or 0) > max_adaptive_quarantined_total:
        reasons.append("adaptive_candidate_quarantine_total_exceeded")
    if int(aggregate.get("adaptive_fallback_quality_degraded_count", 0) or 0) > max_adaptive_fallback_quality_degraded_count:
        reasons.append("adaptive_fallback_quality_degraded_too_often")

    recent_statuses = [str(c.get("promotion_gate_status") or "") for c in campaigns[-require_recent_gate_passes:]] if require_recent_gate_passes > 0 else []
    if require_recent_gate_passes > 0 and len(recent_statuses) < require_recent_gate_passes:
        reasons.append("insufficient_recent_gate_history")
    elif require_recent_gate_passes > 0 and any(s != "pass" for s in recent_statuses):
        reasons.append("recent_campaign_gate_not_all_pass")

    fallback_usage_total = int((aggregate.get("provider_usage_by_provider") or {}).get("candidate_file", 0) or 0)
    provider_failovers_total = int(aggregate.get("provider_failover_count_total", 0) or 0)
    fallback_probe_ok_total = int(aggregate.get("fallback_candidates_probe_ok_total", 0) or 0)
    fallback_probe_failed_total = int(aggregate.get("fallback_candidates_probe_failed_total", 0) or 0)

    if not reasons:
        action = "increase_cap_small_step"
        confidence = "medium"
    elif reasons == ["insufficient_campaign_count"] or reasons == ["insufficient_total_finalized"]:
        action = "continue_tiny_pilots"
        confidence = "medium"
    elif (
        allow_fallback_continue_on_provider_issues
        and "provider_transport_errors_total_exceeded" in reasons
        and "reconciliation_mismatch_total_exceeded" not in reasons
        and fallback_usage_total > 0
        and provider_failovers_total > 0
        and fallback_probe_ok_total >= fallback_probe_failed_total
    ):
        action = "continue_tiny_pilots_with_fallback_source"
        confidence = "medium"
    elif any(r in reasons for r in ("provider_transport_errors_total_exceeded", "reconciliation_mismatch_total_exceeded")):
        action = "hold"
        confidence = "high"
    else:
        action = "continue_tiny_pilots"
        confidence = "low"
    return {"action": action, "confidence": confidence, "reasons": reasons}


def _render_campaign_trend_report_markdown(report: dict[str, Any]) -> str:
    agg = dict(report.get("aggregate") or {})
    rec = dict(report.get("recommendation") or {})
    trends = dict(report.get("trends") or {})
    lines = [
        "# Live Pilot Campaign Trend Report",
        "",
        f"- campaign_count: `{report.get('campaign_count', 0)}`",
        f"- live_finalized_count_total: `{agg.get('live_finalized_count_total', 0)}`",
        f"- live_reconciliation_mismatch_count_total: `{agg.get('live_reconciliation_mismatch_count_total', 0)}`",
        f"- dexscreener_transport_errors_total: `{agg.get('dexscreener_transport_errors_total', 0)}`",
        f"- provider_failover_count_total: `{agg.get('provider_failover_count_total', 0)}`",
        f"- fallback_candidates_probe_failed_total: `{agg.get('fallback_candidates_probe_failed_total', 0)}`",
        f"- adaptive_candidate_quarantined_count_total: `{agg.get('adaptive_candidate_quarantined_count_total', 0)}`",
        f"- adaptive_fallback_quality_degraded_count: `{agg.get('adaptive_fallback_quality_degraded_count', 0)}`",
        f"- worst_realized_slippage_bps_across_campaigns: `{agg.get('worst_realized_slippage_bps_across_campaigns')}`",
        "",
        "## Recommendation",
        "",
        f"- action: `{rec.get('action')}`",
        f"- confidence: `{rec.get('confidence')}`",
        f"- reasons: `{', '.join(list(rec.get('reasons', []) or [])) or '-'}`",
        "",
        "## Trends",
        "",
        f"- promotion_gate_status_sequence: `{json.dumps(trends.get('promotion_gate_status_sequence', []))}`",
        f"- finalized_count_sequence: `{json.dumps(trends.get('finalized_count_sequence', []))}`",
        f"- provider_transport_errors_sequence: `{json.dumps(trends.get('provider_transport_errors_sequence', []))}`",
        f"- provider_failover_count_sequence: `{json.dumps(trends.get('provider_failover_count_sequence', []))}`",
        f"- fallback_probe_failed_sequence: `{json.dumps(trends.get('fallback_probe_failed_sequence', []))}`",
        f"- adaptive_candidate_quarantined_count_sequence: `{json.dumps(trends.get('adaptive_candidate_quarantined_count_sequence', []))}`",
        f"- adaptive_candidate_ttl_expired_count_sequence: `{json.dumps(trends.get('adaptive_candidate_ttl_expired_count_sequence', []))}`",
        f"- stop_reason_sequence: `{json.dumps(trends.get('stop_reason_sequence', []))}`",
        f"- provider_usage_by_provider_total: `{json.dumps(agg.get('provider_usage_by_provider', {}), sort_keys=True)}`",
    ]
    return "\n".join(lines) + "\n"


def write_campaign_trend_report(report: dict[str, Any], path_str: str) -> None:
    path = Path(path_str)
    if path.suffix.lower() in {".md", ".markdown"}:
        path.write_text(_render_campaign_trend_report_markdown(report), encoding="utf-8")
    else:
        path.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_daily_operator_report(
    campaign_reports: list[dict[str, Any]],
    *,
    date_label: str = "",
    recommendation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reports = [dict(r) for r in list(campaign_reports or []) if isinstance(r, dict)]
    trend = aggregate_live_pilot_campaign_reports(reports, recommendation_config=recommendation_config)
    latest_campaign_summary = dict((((reports[-1] if reports else {}) or {}).get("campaign_summary") or {}))
    latest_gate = dict(latest_campaign_summary.get("promotion_gate_summary") or {})
    rec = dict(trend.get("recommendation") or {})
    reasons = [str(x) for x in list(rec.get("reasons") or []) if str(x)]
    checklist = [
        {"item": "Review latest campaign alerts and stop reason", "done": False},
        {"item": "Review reconciliation mismatches and pause latch events", "done": False},
        {"item": "Review provider reliability and fallback probe quality", "done": False},
        {"item": "Review economics/slippage summary for finalized pilots", "done": False},
        {"item": "Approve next cap/frequency step or keep tiny supervised mode", "done": False},
    ]
    recommended_action = str(rec.get("action") or "hold")
    promotion_ready_today = bool(recommended_action == "increase_cap_small_step" and latest_gate.get("status") == "pass")
    decision_status = "manual_review_required"
    if promotion_ready_today:
        decision_status = "eligible_for_operator_promotion_review"
    elif recommended_action in {"hold", "continue_tiny_pilots", "continue_tiny_pilots_with_fallback_source"}:
        decision_status = "continue_supervised_validation"
    operator_decision_summary = {
        "recommended_action": recommended_action,
        "recommendation_confidence": str(rec.get("confidence") or "low"),
        "promotion_ready_today": promotion_ready_today,
        "decision_status": decision_status,
        "blocking_reasons": reasons,
        "latest_campaign_gate_status": str(latest_gate.get("status") or ""),
    }
    return {
        "date_label": str(date_label or ""),
        "campaign_count": len(reports),
        "latest_campaign_summary": latest_campaign_summary,
        "trend_report": trend,
        "operator_decision_summary": operator_decision_summary,
        "operator_checklist": checklist,
        "operator_acknowledgement": {},
    }


def append_live_pilot_operator_decision_log(
    *,
    path_str: str,
    daily_report: dict[str, Any],
    operator_id: str,
    action: str,
    notes: str = "",
    now_unix_ms: int | None = None,
) -> dict[str, Any]:
    if not str(path_str or "").strip():
        return {}
    ts_unix_ms = int(now_unix_ms if now_unix_ms is not None else time.time() * 1000)
    report = dict(daily_report or {})
    decision_summary = dict(report.get("operator_decision_summary") or {})
    row = {
        "ts_unix_ms": ts_unix_ms,
        "event_type": "live_pilot_operator_decision",
        "date_label": str(report.get("date_label") or ""),
        "campaign_count": int(report.get("campaign_count", 0) or 0),
        "operator_id": str(operator_id or ""),
        "action": str(action or ""),
        "notes": str(notes or ""),
        "recommended_action": str(decision_summary.get("recommended_action") or ""),
        "decision_status": str(decision_summary.get("decision_status") or ""),
        "report_recommendation_confidence": str(decision_summary.get("recommendation_confidence") or ""),
    }
    with Path(path_str).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def apply_operator_acknowledgement_to_daily_report(daily_report: dict[str, Any], decision_row: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(daily_report or {})
    ack = dict(decision_row or {})
    if not ack:
        return out
    out["operator_acknowledgement"] = {
        "ts_unix_ms": int(ack.get("ts_unix_ms", 0) or 0),
        "operator_id": str(ack.get("operator_id") or ""),
        "action": str(ack.get("action") or ""),
        "notes": str(ack.get("notes") or ""),
    }
    return out


def _render_live_pilot_daily_operator_report_markdown(report: dict[str, Any]) -> str:
    trend = dict(report.get("trend_report") or {})
    agg = dict(trend.get("aggregate") or {})
    rec = dict(trend.get("recommendation") or {})
    op = dict(report.get("operator_decision_summary") or {})
    latest = dict(report.get("latest_campaign_summary") or {})
    latest_gate = dict(latest.get("promotion_gate_summary") or {})
    checklist = [dict(x) for x in list(report.get("operator_checklist") or []) if isinstance(x, dict)]
    ack = dict(report.get("operator_acknowledgement") or {})
    lines = [
        "# Live Pilot Daily Operator Report",
        "",
        f"- date_label: `{report.get('date_label', '')}`",
        f"- campaign_count: `{report.get('campaign_count', 0)}`",
        f"- recommended_action: `{op.get('recommended_action', '')}`",
        f"- decision_status: `{op.get('decision_status', '')}`",
        f"- promotion_ready_today: `{bool(op.get('promotion_ready_today', False))}`",
        f"- recommendation_confidence: `{op.get('recommendation_confidence', '')}`",
        "",
        "## Latest Campaign",
        "",
        f"- campaign_id: `{latest.get('campaign_id', '')}`",
        f"- completed_runs: `{latest.get('completed_runs', 0)}`",
        f"- stop_reason: `{latest.get('stop_reason', '') or '-'}`",
        f"- promotion_gate_status: `{latest_gate.get('status', '')}`",
        "",
        "## Trend Summary",
        "",
        f"- live_finalized_count_total: `{agg.get('live_finalized_count_total', 0)}`",
        f"- live_reconciliation_mismatch_count_total: `{agg.get('live_reconciliation_mismatch_count_total', 0)}`",
        f"- dexscreener_transport_errors_total: `{agg.get('dexscreener_transport_errors_total', 0)}`",
        f"- provider_failover_count_total: `{agg.get('provider_failover_count_total', 0)}`",
        f"- adaptive_candidate_quarantined_count_total: `{agg.get('adaptive_candidate_quarantined_count_total', 0)}`",
        "",
        "## Recommendation",
        "",
        f"- action: `{rec.get('action', '')}`",
        f"- confidence: `{rec.get('confidence', '')}`",
        f"- reasons: `{', '.join(list(rec.get('reasons', []) or [])) or '-'}`",
        "",
        "## Operator Checklist",
        "",
    ]
    for row in checklist:
        lines.append(f"- [{'x' if bool(row.get('done', False)) else ' '}] {row.get('item', '')}")
    if ack:
        lines.extend(
            [
                "",
                "## Operator Acknowledgement",
                "",
                f"- operator_id: `{ack.get('operator_id', '')}`",
                f"- action: `{ack.get('action', '')}`",
                f"- notes: `{ack.get('notes', '')}`",
                f"- ts_unix_ms: `{ack.get('ts_unix_ms', 0)}`",
            ]
        )
    return "\n".join(lines) + "\n"


def write_live_pilot_daily_operator_report(report: dict[str, Any], path_str: str) -> None:
    path = Path(path_str)
    if path.suffix.lower() in {".md", ".markdown"}:
        path.write_text(_render_live_pilot_daily_operator_report_markdown(report), encoding="utf-8")
    else:
        path.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def _artifact_file_info(path_str: str) -> dict[str, Any]:
    ptxt = str(path_str or "").strip()
    if not ptxt:
        return {"path": "", "present": False}
    p = Path(ptxt)
    if not p.exists():
        return {"path": ptxt, "present": False}
    st = p.stat()
    return {
        "path": ptxt,
        "present": True,
        "size_bytes": int(st.st_size),
        "modified_unix_ms": int(st.st_mtime * 1000),
    }


def build_live_pilot_artifact_index(
    *,
    date_label: str = "",
    schedule_report: dict[str, Any] | None = None,
    schedule_report_path: str = "",
    schedule_state_path: str = "",
    daily_operator_report: dict[str, Any] | None = None,
    daily_operator_report_path: str = "",
    campaign_reports: list[dict[str, Any]] | None = None,
    campaign_report_paths: list[str] | None = None,
    campaign_state_paths: list[str] | None = None,
    alerts_jsonl_path: str = "",
    operator_decision_log_jsonl_path: str = "",
) -> dict[str, Any]:
    campaign_reports = [dict(x) for x in list(campaign_reports or []) if isinstance(x, dict)]
    campaign_report_paths = [str(x) for x in list(campaign_report_paths or []) if str(x)]
    campaign_state_paths = [str(x) for x in list(campaign_state_paths or []) if str(x)]
    schedule_summary = dict(((schedule_report or {}).get("schedule_summary") or {}))
    latest_campaign_summary = {}
    if schedule_report and isinstance((schedule_report or {}).get("sessions"), list) and (schedule_report or {}).get("sessions"):
        latest_campaign_summary = dict((((schedule_report or {}).get("sessions") or [])[-1] or {}).get("campaign_summary") or {})
    elif campaign_reports:
        latest_campaign_summary = dict(((campaign_reports[-1] or {}).get("campaign_summary") or {}))
    daily_summary = dict((daily_operator_report or {}).get("operator_decision_summary") or {})
    index = {
        "date_label": str(date_label or ""),
        "generated_unix_ms": int(time.time() * 1000),
        "schedule_summary": schedule_summary,
        "latest_campaign_summary": latest_campaign_summary,
        "daily_operator_decision_summary": daily_summary,
        "artifacts": {
            "schedule_report": _artifact_file_info(schedule_report_path),
            "schedule_state": _artifact_file_info(schedule_state_path),
            "daily_operator_report": _artifact_file_info(daily_operator_report_path),
            "alerts_jsonl": _artifact_file_info(alerts_jsonl_path),
            "operator_decision_log_jsonl": _artifact_file_info(operator_decision_log_jsonl_path),
            "campaign_reports": [_artifact_file_info(p) for p in campaign_report_paths],
            "campaign_states": [_artifact_file_info(p) for p in campaign_state_paths],
        },
        "counts": {
            "campaign_reports": len(campaign_reports),
            "campaign_report_paths": len(campaign_report_paths),
            "campaign_state_paths": len(campaign_state_paths),
        },
    }
    return index


def _render_live_pilot_artifact_index_markdown(report: dict[str, Any]) -> str:
    arts = dict(report.get("artifacts") or {})
    sched = dict(report.get("schedule_summary") or {})
    op = dict(report.get("daily_operator_decision_summary") or {})
    lines = [
        "# Live Pilot Artifact Index",
        "",
        f"- date_label: `{report.get('date_label', '')}`",
        f"- generated_unix_ms: `{report.get('generated_unix_ms', 0)}`",
        f"- schedule_id: `{sched.get('schedule_id', '')}`",
        f"- completed_sessions: `{sched.get('completed_sessions', 0)}`",
        f"- recommended_action: `{op.get('recommended_action', '')}`",
        "",
        "## Artifacts",
        "",
        f"- schedule_report: `{json.dumps(arts.get('schedule_report', {}), sort_keys=True)}`",
        f"- schedule_state: `{json.dumps(arts.get('schedule_state', {}), sort_keys=True)}`",
        f"- daily_operator_report: `{json.dumps(arts.get('daily_operator_report', {}), sort_keys=True)}`",
        f"- alerts_jsonl: `{json.dumps(arts.get('alerts_jsonl', {}), sort_keys=True)}`",
        f"- operator_decision_log_jsonl: `{json.dumps(arts.get('operator_decision_log_jsonl', {}), sort_keys=True)}`",
        f"- campaign_reports_count: `{len(list(arts.get('campaign_reports') or []))}`",
        f"- campaign_states_count: `{len(list(arts.get('campaign_states') or []))}`",
    ]
    return "\n".join(lines) + "\n"


def write_live_pilot_artifact_index(report: dict[str, Any], path_str: str) -> None:
    path = Path(path_str)
    if path.suffix.lower() in {".md", ".markdown"}:
        path.write_text(_render_live_pilot_artifact_index_markdown(report), encoding="utf-8")
    else:
        path.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_handoff_snapshot(
    *,
    schedule_report: dict[str, Any] | None = None,
    daily_operator_report: dict[str, Any] | None = None,
    artifact_index: dict[str, Any] | None = None,
    handoff_operator_id: str = "",
    shift_label: str = "",
    handoff_notes: str = "",
    restart_command_hint: str = "",
) -> dict[str, Any]:
    schedule_summary = dict(((schedule_report or {}).get("schedule_summary") or {}))
    daily = dict(daily_operator_report or {})
    latest_campaign = dict(daily.get("latest_campaign_summary") or {})
    op = dict(daily.get("operator_decision_summary") or {})
    checklist = [
        {"item": "Verify provider access status (DexScreener/backup source)", "done": False},
        {"item": "Check latest campaign stop reason and alerts", "done": False},
        {"item": "Confirm campaign/schedule state files exist before resume", "done": False},
        {"item": "Review operator decision log and latest acknowledgement", "done": False},
        {"item": "Resume schedule/campaign only after confirming caps and mode", "done": False},
    ]
    return {
        "generated_unix_ms": int(time.time() * 1000),
        "shift_label": str(shift_label or ""),
        "handoff_operator_id": str(handoff_operator_id or ""),
        "handoff_notes": str(handoff_notes or ""),
        "schedule_summary": schedule_summary,
        "latest_campaign_summary": latest_campaign,
        "operator_decision_summary": op,
        "artifact_index_summary": {
            "artifact_index_path": str(((artifact_index or {}).get("artifacts") or {}).get("artifact_index", {}).get("path") or ""),
            "date_label": str((artifact_index or {}).get("date_label") or ""),
        },
        "restart_recovery_checklist": checklist,
        "restart_command_hint": str(restart_command_hint or ""),
    }


def _render_live_pilot_handoff_snapshot_markdown(report: dict[str, Any]) -> str:
    sched = dict(report.get("schedule_summary") or {})
    latest = dict(report.get("latest_campaign_summary") or {})
    op = dict(report.get("operator_decision_summary") or {})
    checklist = [dict(x) for x in list(report.get("restart_recovery_checklist") or []) if isinstance(x, dict)]
    lines = [
        "# Live Pilot Operator Hand-off Snapshot",
        "",
        f"- shift_label: `{report.get('shift_label', '')}`",
        f"- handoff_operator_id: `{report.get('handoff_operator_id', '')}`",
        f"- generated_unix_ms: `{report.get('generated_unix_ms', 0)}`",
        f"- handoff_notes: `{report.get('handoff_notes', '')}`",
        "",
        "## Current State",
        "",
        f"- schedule_id: `{sched.get('schedule_id', '')}`",
        f"- schedule_stop_reason: `{sched.get('stop_reason', '') or '-'}`",
        f"- completed_sessions: `{sched.get('completed_sessions', 0)}`",
        f"- latest_campaign_id: `{latest.get('campaign_id', '')}`",
        f"- latest_campaign_stop_reason: `{latest.get('stop_reason', '') or '-'}`",
        f"- recommended_action: `{op.get('recommended_action', '')}`",
        f"- decision_status: `{op.get('decision_status', '')}`",
        "",
        "## Restart Recovery Checklist",
        "",
    ]
    for row in checklist:
        lines.append(f"- [{'x' if bool(row.get('done', False)) else ' '}] {row.get('item', '')}")
    if str(report.get("restart_command_hint") or "").strip():
        lines.extend(["", "## Restart Command Hint", "", f"`{report.get('restart_command_hint')}`"])
    return "\n".join(lines) + "\n"


def write_live_pilot_handoff_snapshot(report: dict[str, Any], path_str: str) -> None:
    path = Path(path_str)
    if path.suffix.lower() in {".md", ".markdown"}:
        path.write_text(_render_live_pilot_handoff_snapshot_markdown(report), encoding="utf-8")
    else:
        path.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def validate_live_pilot_campaign_state(state: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    s = dict(state or {})
    runs = [dict(x) for x in list(s.get("runs") or []) if isinstance(x, dict)]
    errors: list[str] = []
    warnings: list[str] = []
    run_indexes = []
    sigs = set()
    for row in runs:
        idx = _to_int_or_none(row.get("run_index"))
        if idx is None:
            errors.append("missing_run_index")
            continue
        run_indexes.append(int(idx))
        sig = str(row.get("submitted_signature") or "")
        if sig:
            if sig in sigs:
                warnings.append("duplicate_submitted_signature")
            sigs.add(sig)
    if run_indexes and sorted(run_indexes) != list(range(min(run_indexes), min(run_indexes) + len(run_indexes))):
        errors.append("non_contiguous_run_indexes")
    if len(run_indexes) != len(set(run_indexes)):
        errors.append("duplicate_run_index")
    target_runs = _to_int_or_none(s.get("target_runs"))
    if target_runs is not None and int(target_runs) < len(runs):
        errors.append("runs_exceed_target")
    stop_reason = str(s.get("stop_reason") or "")
    if stop_reason and target_runs is not None and len(runs) >= int(target_runs):
        warnings.append("stop_reason_present_after_target_completed")
    return {
        "ok": (len(errors) == 0 if strict else len(errors) == 0),
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "summary": {
            "runs_count": len(runs),
            "target_runs": (None if target_runs is None else int(target_runs)),
            "stop_reason": stop_reason,
        },
    }


def validate_live_pilot_schedule_state(state: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    s = dict(state or {})
    sessions = [dict(x) for x in list(s.get("sessions") or []) if isinstance(x, dict)]
    errors: list[str] = []
    warnings: list[str] = []
    idxs = []
    campaign_ids = set()
    for row in sessions:
        idx = _to_int_or_none(row.get("session_index"))
        if idx is None:
            errors.append("missing_session_index")
            continue
        idxs.append(int(idx))
        cid = str(row.get("campaign_id") or "")
        if cid:
            if cid in campaign_ids:
                warnings.append("duplicate_campaign_id")
            campaign_ids.add(cid)
    if len(idxs) != len(set(idxs)):
        errors.append("duplicate_session_index")
    if idxs and sorted(idxs) != list(range(min(idxs), min(idxs) + len(idxs))):
        errors.append("non_contiguous_session_indexes")
    target_sessions = _to_int_or_none(s.get("target_sessions"))
    if target_sessions is not None and int(target_sessions) < len(sessions):
        errors.append("sessions_exceed_target")
    stop_reason = str(s.get("stop_reason") or "")
    if stop_reason and target_sessions is not None and len(sessions) >= int(target_sessions):
        warnings.append("stop_reason_present_after_target_completed")
    return {
        "ok": (len(errors) == 0 if strict else len(errors) == 0),
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "summary": {
            "sessions_count": len(sessions),
            "target_sessions": (None if target_sessions is None else int(target_sessions)),
            "stop_reason": stop_reason,
        },
    }


def build_live_pilot_run_manifest(*, args_namespace: Any, argv: list[str] | None = None, phase: str = "pre_run") -> dict[str, Any]:
    args_dict = {}
    if hasattr(args_namespace, "__dict__"):
        args_dict = {str(k): v for k, v in vars(args_namespace).items()}
    redacted = dict(args_dict)
    for k in list(redacted.keys()):
        if "token" in k.lower() and k.lower() not in {"token_address"}:
            redacted[k] = "***"
    return {
        "generated_unix_ms": int(time.time() * 1000),
        "phase": str(phase or ""),
        "mode": str(redacted.get("mode") or ""),
        "args": redacted,
        "argv": [str(x) for x in list(argv or [])],
        "repro_command": " ".join([str(x) for x in (["python", "-m", "src.live.live_pilot_service"] + list(argv or []))]),
    }


def write_live_pilot_run_manifest(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = [
            "# Live Pilot Run Manifest",
            "",
            f"- generated_unix_ms: `{report.get('generated_unix_ms', 0)}`",
            f"- phase: `{report.get('phase', '')}`",
            f"- mode: `{report.get('mode', '')}`",
            "",
            "## Repro Command",
            "",
            f"`{report.get('repro_command', '')}`",
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def verify_live_pilot_validation_bundle(
    *,
    artifact_index: dict[str, Any] | None = None,
    artifact_index_path: str = "",
) -> dict[str, Any]:
    idx = dict(artifact_index or {})
    if not idx and str(artifact_index_path or "").strip():
        p = Path(artifact_index_path)
        if p.exists() and p.suffix.lower() not in {".md", ".markdown"}:
            try:
                idx = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                idx = {}
    arts = dict(idx.get("artifacts") or {})
    checks: list[dict[str, Any]] = []
    for key in ("schedule_report", "schedule_state", "daily_operator_report", "alerts_jsonl", "operator_decision_log_jsonl"):
        info = dict(arts.get(key) or {})
        present = bool(info.get("present", False))
        optional = key in {"alerts_jsonl", "operator_decision_log_jsonl"}
        checks.append({"name": key, "ok": (present or optional), "present": present, "optional": optional, "path": str(info.get("path") or "")})
    for key in ("campaign_reports", "campaign_states"):
        rows = [dict(x) for x in list(arts.get(key) or []) if isinstance(x, dict)]
        present_n = sum(1 for r in rows if bool(r.get("present", False)))
        checks.append({"name": key, "ok": present_n == len(rows), "present_count": present_n, "total_count": len(rows)})
    failed = [c["name"] for c in checks if not bool(c.get("ok", False))]
    return {"status": ("pass" if not failed else "fail"), "checks": checks, "failed_checks": failed}


def write_live_pilot_bundle_verification(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = ["# Live Pilot Validation Bundle Verification", "", f"- status: `{report.get('status', '')}`", ""]
        for c in [dict(x) for x in list(report.get("checks") or []) if isinstance(x, dict)]:
            lines.append(f"- {c.get('name','')}: `{'pass' if c.get('ok') else 'fail'}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_session_timeline(
    *,
    schedule_report: dict[str, Any] | None = None,
    alerts_rows: list[dict[str, Any]] | None = None,
    operator_decision_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    sched = dict(((schedule_report or {}).get("schedule_summary") or {}))
    if sched:
        events.append({"ts_unix_ms": 0, "event_type": "schedule_summary", "details": sched})
    for sess in [dict(x) for x in list((schedule_report or {}).get("sessions") or []) if isinstance(x, dict)]:
        csum = dict(sess.get("campaign_summary") or {})
        events.append(
            {
                "ts_unix_ms": int(sess.get("session_index", 0) or 0),
                "event_type": "campaign_session",
                "details": {
                    "session_index": sess.get("session_index"),
                    "campaign_id": sess.get("campaign_id"),
                    "stop_reason": csum.get("stop_reason"),
                    "promotion_gate_status": dict(csum.get("promotion_gate_summary") or {}).get("status"),
                },
            }
        )
    for row in [dict(x) for x in list(alerts_rows or []) if isinstance(x, dict)]:
        events.append(
            {
                "ts_unix_ms": int(row.get("ts_unix_ms", 0) or 0),
                "event_type": "campaign_alert",
                "details": {"alert_type": row.get("alert_type"), "level": row.get("level"), "message": row.get("message")},
            }
        )
    for row in [dict(x) for x in list(operator_decision_rows or []) if isinstance(x, dict)]:
        events.append(
            {
                "ts_unix_ms": int(row.get("ts_unix_ms", 0) or 0),
                "event_type": "operator_decision",
                "details": {"operator_id": row.get("operator_id"), "action": row.get("action"), "notes": row.get("notes")},
            }
        )
    events_sorted = sorted(events, key=lambda x: (int(x.get("ts_unix_ms", 0) or 0), str(x.get("event_type") or "")))
    breadcrumbs = [
        e for e in events_sorted
        if (e.get("event_type") == "campaign_alert" and str(((e.get("details") or {}).get("level") or "")).lower() == "critical")
        or (e.get("event_type") == "operator_decision")
        or (e.get("event_type") == "campaign_session" and str(((e.get("details") or {}).get("stop_reason") or "")))
    ]
    return {"event_count": len(events_sorted), "events": events_sorted, "incident_breadcrumbs": breadcrumbs}


def write_live_pilot_session_timeline(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = ["# Live Pilot Session Timeline", "", f"- event_count: `{report.get('event_count', 0)}`", "", "## Incident Breadcrumbs", ""]
        for e in [dict(x) for x in list(report.get("incident_breadcrumbs") or []) if isinstance(x, dict)]:
            lines.append(f"- {e.get('event_type')}: `{json.dumps(e.get('details', {}), sort_keys=True)}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def _read_jsonl_rows(path_str: str) -> list[dict[str, Any]]:
    if not str(path_str or "").strip():
        return []
    p = Path(path_str)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def get_live_pilot_risk_profile_preset(name: str) -> dict[str, Any]:
    key = str(name or "").strip().lower()
    presets = {
        "tiny_supervised": {
            "profile_name": "tiny_supervised",
            "max_notional_usd_total": 1.0,
            "max_orders_per_session": 1,
            "schedule_sessions_per_day_cap": 3,
            "requires_operator_acknowledgement": True,
            "requires_bundle_verification_pass": True,
            "target_mode": "pilot_campaign_tiny_supervised",
        },
        "tiny_supervised_plus": {
            "profile_name": "tiny_supervised_plus",
            "max_notional_usd_total": 2.0,
            "max_orders_per_session": 1,
            "schedule_sessions_per_day_cap": 5,
            "requires_operator_acknowledgement": True,
            "requires_bundle_verification_pass": True,
            "target_mode": "pilot_campaign_tiny_supervised",
        },
        "frequency_step_only": {
            "profile_name": "frequency_step_only",
            "max_notional_usd_total": 1.0,
            "max_orders_per_session": 1,
            "schedule_sessions_per_day_cap": 8,
            "requires_operator_acknowledgement": True,
            "requires_bundle_verification_pass": True,
            "target_mode": "pilot_campaign_schedule_tiny_supervised",
        },
    }
    if key not in presets:
        raise ValueError(f"unknown risk profile preset: {name}")
    return dict(presets[key])


def build_live_pilot_promotion_step_manifest(
    *,
    risk_profile_preset: str,
    step_name: str = "",
    daily_operator_report: dict[str, Any] | None = None,
    artifact_index: dict[str, Any] | None = None,
    bundle_verification: dict[str, Any] | None = None,
    operator_decision_log_path: str = "",
) -> dict[str, Any]:
    preset = get_live_pilot_risk_profile_preset(risk_profile_preset)
    daily = dict(daily_operator_report or {})
    op = dict(daily.get("operator_decision_summary") or {})
    ack = dict(daily.get("operator_acknowledgement") or {})
    bundle = dict(bundle_verification or {})
    idx = dict(artifact_index or {})
    recommended_action = str(op.get("recommended_action") or "")
    target_step = str(step_name or recommended_action or "hold")
    return {
        "generated_unix_ms": int(time.time() * 1000),
        "step_name": target_step,
        "risk_profile_preset": preset["profile_name"],
        "risk_profile": preset,
        "operator_context": {
            "recommended_action": recommended_action,
            "decision_status": str(op.get("decision_status") or ""),
            "operator_acknowledged": bool(ack),
            "operator_ack_action": str(ack.get("action") or ""),
            "operator_decision_log_path": str(operator_decision_log_path or ""),
        },
        "artifact_context": {
            "artifact_index_path": str(((idx.get("artifacts") or {}).get("artifact_index") or {}).get("path") or ""),
            "bundle_verification_status": str(bundle.get("status") or ""),
            "bundle_failed_checks": list(bundle.get("failed_checks", []) or []),
        },
    }


def write_live_pilot_promotion_step_manifest(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        rp = dict(report.get("risk_profile") or {})
        op = dict(report.get("operator_context") or {})
        ac = dict(report.get("artifact_context") or {})
        lines = [
            "# Live Pilot Promotion Step Manifest",
            "",
            f"- generated_unix_ms: `{report.get('generated_unix_ms', 0)}`",
            f"- step_name: `{report.get('step_name', '')}`",
            f"- risk_profile_preset: `{report.get('risk_profile_preset', '')}`",
            "",
            "## Risk Profile",
            "",
            f"- max_notional_usd_total: `{rp.get('max_notional_usd_total')}`",
            f"- max_orders_per_session: `{rp.get('max_orders_per_session')}`",
            f"- schedule_sessions_per_day_cap: `{rp.get('schedule_sessions_per_day_cap')}`",
            "",
            "## Operator Context",
            "",
            f"- recommended_action: `{op.get('recommended_action', '')}`",
            f"- decision_status: `{op.get('decision_status', '')}`",
            f"- operator_acknowledged: `{bool(op.get('operator_acknowledged', False))}`",
            "",
            "## Artifact Context",
            "",
            f"- bundle_verification_status: `{ac.get('bundle_verification_status', '')}`",
            f"- bundle_failed_checks: `{', '.join(list(ac.get('bundle_failed_checks', []) or [])) or '-'}`",
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_prelive_go_no_go_checklist(
    *,
    daily_operator_report: dict[str, Any] | None = None,
    bundle_verification: dict[str, Any] | None = None,
    handoff_snapshot: dict[str, Any] | None = None,
    risk_profile_preset: str = "",
    required_operator_ack: bool = True,
    require_bundle_pass: bool = True,
) -> dict[str, Any]:
    daily = dict(daily_operator_report or {})
    op = dict(daily.get("operator_decision_summary") or {})
    ack = dict(daily.get("operator_acknowledgement") or {})
    bundle = dict(bundle_verification or {})
    handoff = dict(handoff_snapshot or {})
    profile = (get_live_pilot_risk_profile_preset(risk_profile_preset) if str(risk_profile_preset or "").strip() else {})
    checks = [
        {
            "name": "bundle_verification_pass",
            "ok": (str(bundle.get("status") or "") == "pass") if require_bundle_pass else True,
            "required": bool(require_bundle_pass),
            "actual": str(bundle.get("status") or ""),
        },
        {
            "name": "operator_acknowledged",
            "ok": bool(ack) if required_operator_ack else True,
            "required": bool(required_operator_ack),
            "actual": bool(ack),
        },
        {
            "name": "decision_not_hold",
            "ok": str(ack.get("action") or op.get("recommended_action") or "") != "hold",
            "required": False,
            "actual": str(ack.get("action") or op.get("recommended_action") or ""),
        },
        {
            "name": "handoff_snapshot_present",
            "ok": bool(handoff),
            "required": False,
            "actual": bool(handoff),
        },
    ]
    failed_required = [c["name"] for c in checks if bool(c.get("required", False)) and not bool(c.get("ok", False))]
    status = "go" if not failed_required else "no_go"
    return {
        "generated_unix_ms": int(time.time() * 1000),
        "status": status,
        "failed_required_checks": failed_required,
        "risk_profile_preset": str(profile.get("profile_name") or ""),
        "risk_profile": profile,
        "checks": checks,
        "operator_decision_summary": op,
        "operator_acknowledgement": ack,
        "bundle_verification_status": str(bundle.get("status") or ""),
    }


def write_live_pilot_prelive_go_no_go_checklist(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = [
            "# Live Pilot Pre-Live Go / No-Go Checklist",
            "",
            f"- status: `{report.get('status', '')}`",
            f"- risk_profile_preset: `{report.get('risk_profile_preset', '')}`",
            f"- bundle_verification_status: `{report.get('bundle_verification_status', '')}`",
            f"- failed_required_checks: `{', '.join(list(report.get('failed_required_checks', []) or [])) or '-'}`",
            "",
            "## Checks",
            "",
        ]
        for c in [dict(x) for x in list(report.get("checks") or []) if isinstance(x, dict)]:
            lines.append(f"- {c.get('name','')}: `{'pass' if c.get('ok') else 'fail'}` required=`{bool(c.get('required', False))}` actual=`{c.get('actual')}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_postrun_review_packet(
    *,
    schedule_report: dict[str, Any] | None = None,
    daily_operator_report: dict[str, Any] | None = None,
    artifact_index: dict[str, Any] | None = None,
    handoff_snapshot: dict[str, Any] | None = None,
    bundle_verification: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schedule = dict(schedule_report or {})
    daily = dict(daily_operator_report or {})
    idx = dict(artifact_index or {})
    handoff = dict(handoff_snapshot or {})
    bundle = dict(bundle_verification or {})
    tl = dict(timeline or {})
    schedule_summary = dict(schedule.get("schedule_summary") or {})
    daily_op = dict(daily.get("operator_decision_summary") or {})
    latest_campaign = dict(daily.get("latest_campaign_summary") or {})
    return {
        "generated_unix_ms": int(time.time() * 1000),
        "summary": {
            "schedule_id": str(schedule_summary.get("schedule_id") or ""),
            "completed_sessions": int(schedule_summary.get("completed_sessions", 0) or 0),
            "latest_campaign_id": str(latest_campaign.get("campaign_id") or ""),
            "recommended_action": str(daily_op.get("recommended_action") or ""),
            "decision_status": str(daily_op.get("decision_status") or ""),
            "bundle_verification_status": str(bundle.get("status") or ""),
            "timeline_event_count": int(tl.get("event_count", 0) or 0),
        },
        "artifact_index_summary": {
            "date_label": str(idx.get("date_label") or ""),
            "artifact_count_groups": len(dict(idx.get("artifacts") or {})),
        },
        "checkpoints": [
            {"name": "bundle_verification", "status": str(bundle.get("status") or "")},
            {"name": "operator_decision", "status": str(daily_op.get("decision_status") or "")},
            {"name": "handoff_snapshot_present", "status": ("present" if bool(handoff) else "missing")},
            {"name": "timeline_present", "status": ("present" if bool(tl) else "missing")},
        ],
    }


def write_live_pilot_postrun_review_packet(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        s = dict(report.get("summary") or {})
        lines = [
            "# Live Pilot Post-Run Review Packet",
            "",
            f"- schedule_id: `{s.get('schedule_id', '')}`",
            f"- completed_sessions: `{s.get('completed_sessions', 0)}`",
            f"- latest_campaign_id: `{s.get('latest_campaign_id', '')}`",
            f"- recommended_action: `{s.get('recommended_action', '')}`",
            f"- decision_status: `{s.get('decision_status', '')}`",
            f"- bundle_verification_status: `{s.get('bundle_verification_status', '')}`",
            f"- timeline_event_count: `{s.get('timeline_event_count', 0)}`",
            "",
            "## Checkpoints",
            "",
        ]
        for c in [dict(x) for x in list(report.get("checkpoints") or []) if isinstance(x, dict)]:
            lines.append(f"- {c.get('name','')}: `{c.get('status','')}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def rotate_live_pilot_artifacts_by_glob(
    *,
    glob_pattern: str,
    archive_dir: str,
    keep_latest: int = 10,
) -> dict[str, Any]:
    pattern = str(glob_pattern or "").strip()
    if not pattern:
        return {"matched": 0, "kept": 0, "archived": 0, "archived_paths": []}
    keep_latest = max(0, int(keep_latest))
    matches = [p for p in Path(".").glob(pattern) if p.is_file()]
    matches_sorted = sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)
    keep = matches_sorted[:keep_latest]
    archive = matches_sorted[keep_latest:]
    archive_path = Path(archive_dir)
    archive_path.mkdir(parents=True, exist_ok=True)
    archived_paths: list[str] = []
    for p in archive:
        dst = archive_path / p.name
        if dst.exists():
            dst = archive_path / f"{p.stem}_{int(time.time()*1000)}{p.suffix}"
        shutil.move(str(p), str(dst))
        archived_paths.append(str(dst))
    return {
        "matched": len(matches_sorted),
        "kept": len(keep),
        "archived": len(archive),
        "archived_paths": archived_paths,
        "glob_pattern": pattern,
        "archive_dir": str(archive_dir),
    }


def write_live_pilot_archive_rotation_report(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = [
            "# Live Pilot Archive Rotation Report",
            "",
            f"- glob_pattern: `{report.get('glob_pattern','')}`",
            f"- matched: `{report.get('matched',0)}`",
            f"- kept: `{report.get('kept',0)}`",
            f"- archived: `{report.get('archived',0)}`",
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_launch_intent_manifest(
    *,
    mode: str,
    risk_profile_preset: str = "",
    enable_live_auto_submit_window: bool = False,
    adapter_config: dict[str, Any] | None = None,
    prelive_go_no_go_report: dict[str, Any] | None = None,
    expires_in_seconds: float = 1800.0,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    cfg = dict(adapter_config or {})
    go_no_go = dict(prelive_go_no_go_report or {})
    scope = {
        "mode": str(mode or ""),
        "risk_profile_preset": str(risk_profile_preset or ""),
        "live_send_network_enabled": bool(cfg.get("live_send_network_enabled", False)),
        "enable_live_auto_submit_window": bool(enable_live_auto_submit_window),
        "prelive_status": str(go_no_go.get("status") or ""),
        "bundle_verification_status": str(go_no_go.get("bundle_verification_status") or ""),
    }
    scope_json = json.dumps(scope, sort_keys=True)
    scope_hash = hashlib.sha256(scope_json.encode("utf-8")).hexdigest()
    return {
        "generated_unix_ms": now_ms,
        "expires_unix_ms": now_ms + int(max(0.0, float(expires_in_seconds)) * 1000.0),
        "scope": scope,
        "scope_hash_sha256": scope_hash,
        "intent_id": "lpi_" + scope_hash[:16],
    }


def write_live_pilot_launch_intent_manifest(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        scope = dict(report.get("scope") or {})
        lines = [
            "# Live Pilot Launch Intent Manifest",
            "",
            f"- intent_id: `{report.get('intent_id', '')}`",
            f"- generated_unix_ms: `{report.get('generated_unix_ms', 0)}`",
            f"- expires_unix_ms: `{report.get('expires_unix_ms', 0)}`",
            f"- scope_hash_sha256: `{report.get('scope_hash_sha256', '')}`",
            "",
            "## Scope",
            "",
        ]
        for k in sorted(scope.keys()):
            lines.append(f"- {k}: `{scope.get(k)}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_promotion_ticket(
    *,
    operator_id: str,
    approval_action: str,
    risk_profile_preset: str = "",
    promotion_step_manifest: dict[str, Any] | None = None,
    prelive_go_no_go_report: dict[str, Any] | None = None,
    launch_intent_manifest: dict[str, Any] | None = None,
    expires_in_seconds: float = 3600.0,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    manifest = dict(promotion_step_manifest or {})
    go_no_go = dict(prelive_go_no_go_report or {})
    launch_intent = dict(launch_intent_manifest or {})
    intent_scope = dict(launch_intent.get("scope") or {})
    payload = {
        "issued_unix_ms": now_ms,
        "expires_unix_ms": now_ms + int(max(0.0, float(expires_in_seconds)) * 1000.0),
        "operator_id": str(operator_id or ""),
        "approval_action": str(approval_action or ""),
        "risk_profile_preset": str(risk_profile_preset or ""),
        "promotion_step_name": str(manifest.get("step_name") or ""),
        "prelive_status": str(go_no_go.get("status") or ""),
        "failed_required_checks": list(go_no_go.get("failed_required_checks", []) or []),
        "intent_id": str(launch_intent.get("intent_id") or ""),
        "intent_scope_hash_sha256": str(launch_intent.get("scope_hash_sha256") or ""),
        "intent_mode": str(intent_scope.get("mode") or ""),
        "intent_live_send_network_enabled": bool(intent_scope.get("live_send_network_enabled", False)),
        "intent_enable_live_auto_submit_window": bool(intent_scope.get("enable_live_auto_submit_window", False)),
    }
    fingerprint_src = json.dumps(payload, sort_keys=True)
    payload["ticket_id"] = "lpt_" + hashlib.sha256(fingerprint_src.encode("utf-8")).hexdigest()[:16]
    payload["ticket_fingerprint_sha256"] = hashlib.sha256(fingerprint_src.encode("utf-8")).hexdigest()
    return payload


def write_live_pilot_promotion_ticket(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = [
            "# Live Pilot Promotion Ticket",
            "",
            f"- ticket_id: `{report.get('ticket_id', '')}`",
            f"- operator_id: `{report.get('operator_id', '')}`",
            f"- approval_action: `{report.get('approval_action', '')}`",
            f"- risk_profile_preset: `{report.get('risk_profile_preset', '')}`",
            f"- promotion_step_name: `{report.get('promotion_step_name', '')}`",
            f"- prelive_status: `{report.get('prelive_status', '')}`",
            f"- expires_unix_ms: `{report.get('expires_unix_ms', 0)}`",
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def _read_json_or_empty(path_str: str) -> dict[str, Any]:
    if not str(path_str or "").strip():
        return {}
    p = Path(path_str)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def list_live_pilot_promotion_ticket_consumptions(consumption_log_jsonl_path: str) -> list[dict[str, Any]]:
    return [dict(x) for x in _read_jsonl_rows(str(consumption_log_jsonl_path or "")) if isinstance(x, dict) and str(x.get("event_type") or "") == "live_pilot_promotion_ticket_consumed"]


def list_live_pilot_promotion_ticket_revocations(revocation_log_jsonl_path: str) -> list[dict[str, Any]]:
    return [dict(x) for x in _read_jsonl_rows(str(revocation_log_jsonl_path or "")) if isinstance(x, dict) and str(x.get("event_type") or "") == "live_pilot_promotion_ticket_revoked"]


def list_live_pilot_launch_authorization_packet_approval_token_revocations(revocation_log_jsonl_path: str) -> list[dict[str, Any]]:
    return [
        dict(x)
        for x in _read_jsonl_rows(str(revocation_log_jsonl_path or ""))
        if isinstance(x, dict) and str(x.get("event_type") or "") == "live_pilot_launch_authorization_packet_approval_token_revoked"
    ]


def resolve_live_pilot_promotion_ticket_latest_state(
    *,
    ticket: dict[str, Any] | None = None,
    consumed_tickets: list[dict[str, Any]] | None = None,
    revoked_tickets: list[dict[str, Any]] | None = None,
    revocation_reason_class_policy_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    t = dict(ticket or {})
    ticket_id = str(t.get("ticket_id") or "")
    ticket_fp = str(t.get("ticket_fingerprint_sha256") or "")
    consumed_rows = [dict(x) for x in list(consumed_tickets or []) if isinstance(x, dict)]
    revoked_rows = [dict(x) for x in list(revoked_tickets or []) if isinstance(x, dict)]
    matching_consumed = [
        dict(r)
        for r in consumed_rows
        if (
            (ticket_id and str(r.get("ticket_id") or "") == ticket_id)
            or (ticket_fp and str(r.get("ticket_fingerprint_sha256") or "") == ticket_fp)
        )
    ]
    matching_revoked = [
        dict(r)
        for r in revoked_rows
        if (
            (ticket_id and str(r.get("ticket_id") or "") == ticket_id)
            or (ticket_fp and str(r.get("ticket_fingerprint_sha256") or "") == ticket_fp)
        )
    ]
    matching_consumed.sort(key=lambda r: int(_to_int_or_none(r.get("ts_unix_ms")) or 0), reverse=True)
    matching_revoked.sort(key=lambda r: int(_to_int_or_none(r.get("ts_unix_ms")) or 0), reverse=True)
    latest_consumption = dict(matching_consumed[0]) if matching_consumed else {}
    latest_revocation = dict(matching_revoked[0]) if matching_revoked else {}
    rev_reason = str(latest_revocation.get("reason") or "")
    rev_reason_class = str(latest_revocation.get("reason_class") or "")
    if latest_revocation and not rev_reason_class:
        rev_reason_class = str(classify_live_pilot_promotion_ticket_revocation_reason(rev_reason).get("reason_class") or "other")
    policy = {
        str(k).strip().lower(): str(v).strip().lower()
        for k, v in dict(revocation_reason_class_policy_overrides or {}).items()
        if str(k).strip() and str(v).strip().lower() in {"allow", "block"}
    }
    revocation_policy_action = str(policy.get(rev_reason_class.lower()) or "block") if latest_revocation else "block"
    revoked = bool(matching_revoked)
    consumed = bool(matching_consumed)
    effective_revoked = bool(revoked and revocation_policy_action != "allow")
    events = []
    if latest_consumption:
        events.append({"kind": "consumed", "ts_unix_ms": int(_to_int_or_none(latest_consumption.get("ts_unix_ms")) or 0)})
    if latest_revocation:
        events.append({"kind": "revoked", "ts_unix_ms": int(_to_int_or_none(latest_revocation.get("ts_unix_ms")) or 0)})
    events.sort(key=lambda e: int(e.get("ts_unix_ms") or 0), reverse=True)
    latest_event_kind = str((events[0] if events else {}).get("kind") or "none")
    latest_state = "issued"
    if effective_revoked:
        latest_state = "revoked"
    elif consumed:
        latest_state = "consumed"
    elif revoked and not effective_revoked:
        latest_state = "revoked_waived"
    return {
        "ticket_id": ticket_id,
        "ticket_fingerprint_sha256": ticket_fp,
        "consumed_count": len(matching_consumed),
        "revoked_count": len(matching_revoked),
        "consumed": consumed,
        "revoked": revoked,
        "effective_revoked": effective_revoked,
        "latest_event_kind": latest_event_kind,
        "latest_state": latest_state,
        "latest_consumption": latest_consumption,
        "latest_revocation": latest_revocation,
        "revocation_reason": rev_reason,
        "revocation_reason_class": rev_reason_class,
        "revocation_policy_action": revocation_policy_action,
    }


def build_live_pilot_promotion_ticket_revocation_audit_summary(
    *,
    ticket: dict[str, Any] | None = None,
    consumed_tickets: list[dict[str, Any]] | None = None,
    revoked_tickets: list[dict[str, Any]] | None = None,
    revocation_reason_class_policy_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    t = dict(ticket or {})
    state = resolve_live_pilot_promotion_ticket_latest_state(
        ticket=t,
        consumed_tickets=consumed_tickets,
        revoked_tickets=revoked_tickets,
        revocation_reason_class_policy_overrides=revocation_reason_class_policy_overrides,
    )
    rev_rows = [dict(x) for x in list(revoked_tickets or []) if isinstance(x, dict)]
    class_counts: dict[str, int] = {}
    sev_counts: dict[str, int] = {}
    for row in rev_rows:
        rclass = str(row.get("reason_class") or classify_live_pilot_promotion_ticket_revocation_reason(str(row.get("reason") or "")).get("reason_class") or "other")
        sev = str(row.get("severity") or classify_live_pilot_promotion_ticket_revocation_reason(str(row.get("reason") or "")).get("severity") or "warning")
        class_counts[rclass] = int(class_counts.get(rclass, 0)) + 1
        sev_counts[sev] = int(sev_counts.get(sev, 0)) + 1
    return {
        "generated_unix_ms": int(time.time() * 1000),
        "ticket_id": str(t.get("ticket_id") or ""),
        "ticket_fingerprint_sha256": str(t.get("ticket_fingerprint_sha256") or ""),
        "ticket_state": state,
        "revocation_reason_class_counts": class_counts,
        "revocation_severity_counts": sev_counts,
        "consumption_events_total": len(list(consumed_tickets or [])),
        "revocation_events_total": len(list(revoked_tickets or [])),
    }


def write_live_pilot_promotion_ticket_revocation_audit_summary(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        st = dict(report.get("ticket_state") or {})
        lines = [
            "# Live Pilot Promotion Ticket Revocation Audit",
            "",
            f"- ticket_id: `{report.get('ticket_id', '')}`",
            f"- latest_state: `{st.get('latest_state', '')}`",
            f"- latest_event_kind: `{st.get('latest_event_kind', '')}`",
            f"- revoked: `{bool(st.get('revoked', False))}`",
            f"- effective_revoked: `{bool(st.get('effective_revoked', False))}`",
            f"- revocation_reason_class: `{st.get('revocation_reason_class', '')}`",
            f"- revocation_policy_action: `{st.get('revocation_policy_action', '')}`",
            "",
            "## Counts",
            "",
            f"- consumption_events_total: `{report.get('consumption_events_total', 0)}`",
            f"- revocation_events_total: `{report.get('revocation_events_total', 0)}`",
            f"- revocation_reason_class_counts: `{json.dumps(report.get('revocation_reason_class_counts', {}), sort_keys=True)}`",
            f"- revocation_severity_counts: `{json.dumps(report.get('revocation_severity_counts', {}), sort_keys=True)}`",
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def classify_live_pilot_promotion_ticket_revocation_reason(reason: str) -> dict[str, str]:
    text = str(reason or "").strip().lower()
    if text in {"security_compromise", "credential_compromise", "unauthorized_use", "suspected_compromise"}:
        return {"reason_class": "security", "severity": "critical"}
    if text in {"superseded", "duplicate_ticket", "replaced_by_new_ticket"}:
        return {"reason_class": "superseded", "severity": "info"}
    if text in {"stale_prelive", "policy_changed", "risk_profile_changed", "expired_workflow"}:
        return {"reason_class": "policy", "severity": "warning"}
    if text in {"operator_cancelled", "manual_revoke", "shift_handoff_cancel"}:
        return {"reason_class": "operator", "severity": "warning"}
    return {"reason_class": "other", "severity": "warning"}


def _parse_simple_policy_overrides(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in list(pairs or []):
        item = str(raw or "").strip()
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        k = str(key or "").strip().lower()
        v = str(value or "").strip().lower()
        if not k or v not in {"allow", "block"}:
            continue
        out[k] = v
    return out


def consume_live_pilot_promotion_ticket(
    *,
    consumption_log_jsonl_path: str,
    ticket: dict[str, Any] | None,
    reason: str = "live_launch_guard_allow",
) -> dict[str, Any]:
    t = dict(ticket or {})
    if not str(consumption_log_jsonl_path or "").strip() or not t:
        return {}
    row = {
        "ts_unix_ms": int(time.time() * 1000),
        "event_type": "live_pilot_promotion_ticket_consumed",
        "ticket_id": str(t.get("ticket_id") or ""),
        "ticket_fingerprint_sha256": str(t.get("ticket_fingerprint_sha256") or ""),
        "operator_id": str(t.get("operator_id") or ""),
        "approval_action": str(t.get("approval_action") or ""),
        "reason": str(reason or ""),
    }
    with Path(consumption_log_jsonl_path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def revoke_live_pilot_promotion_ticket(
    *,
    revocation_log_jsonl_path: str,
    ticket: dict[str, Any] | None,
    operator_id: str = "",
    reason: str = "manual_revoke",
) -> dict[str, Any]:
    t = dict(ticket or {})
    if not str(revocation_log_jsonl_path or "").strip() or not t:
        return {}
    reason_text = str(reason or "manual_revoke")
    reason_meta = classify_live_pilot_promotion_ticket_revocation_reason(reason_text)
    row = {
        "ts_unix_ms": int(time.time() * 1000),
        "event_type": "live_pilot_promotion_ticket_revoked",
        "ticket_id": str(t.get("ticket_id") or ""),
        "ticket_fingerprint_sha256": str(t.get("ticket_fingerprint_sha256") or ""),
        "operator_id": str(operator_id or t.get("operator_id") or ""),
        "approval_action": str(t.get("approval_action") or ""),
        "reason": reason_text,
        "reason_class": str(reason_meta.get("reason_class") or "other"),
        "severity": str(reason_meta.get("severity") or "warning"),
    }
    with Path(revocation_log_jsonl_path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def revoke_live_pilot_launch_authorization_packet_approval_token(
    *,
    revocation_log_jsonl_path: str,
    approval_token: dict[str, Any] | None,
    operator_id: str = "",
    reason: str = "manual_revoke",
) -> dict[str, Any]:
    tok = dict(approval_token or {})
    if not str(revocation_log_jsonl_path or "").strip() or not tok:
        return {}
    reason_text = str(reason or "manual_revoke")
    reason_meta = classify_live_pilot_promotion_ticket_revocation_reason(reason_text)
    row = {
        "ts_unix_ms": int(time.time() * 1000),
        "event_type": "live_pilot_launch_authorization_packet_approval_token_revoked",
        "token_id": str(tok.get("token_id") or ""),
        "token_fingerprint_sha256": str(tok.get("token_fingerprint_sha256") or ""),
        "authorization_packet_fingerprint_sha256": str(tok.get("authorization_packet_fingerprint_sha256") or ""),
        "operator_id": str(operator_id or tok.get("operator_id") or ""),
        "approval_action": str(tok.get("approval_action") or ""),
        "reason": reason_text,
        "reason_class": str(reason_meta.get("reason_class") or "other"),
        "severity": str(reason_meta.get("severity") or "warning"),
    }
    with Path(revocation_log_jsonl_path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def resolve_live_pilot_launch_authorization_packet_approval_token_latest_state(
    *,
    approval_token: dict[str, Any] | None = None,
    revoked_approval_tokens: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tok = dict(approval_token or {})
    token_id = str(tok.get("token_id") or "")
    token_fp = str(tok.get("token_fingerprint_sha256") or "")
    rev_rows = [dict(x) for x in list(revoked_approval_tokens or []) if isinstance(x, dict)]
    matching = [
        dict(r)
        for r in rev_rows
        if (
            (token_id and str(r.get("token_id") or "") == token_id)
            or (token_fp and str(r.get("token_fingerprint_sha256") or "") == token_fp)
        )
    ]
    matching.sort(key=lambda r: int(_to_int_or_none(r.get("ts_unix_ms")) or 0), reverse=True)
    latest_revocation = dict(matching[0]) if matching else {}
    revoked = bool(matching)
    return {
        "token_id": token_id,
        "token_fingerprint_sha256": token_fp,
        "revoked": revoked,
        "revoked_count": len(matching),
        "latest_state": ("revoked" if revoked else "issued"),
        "latest_revocation": latest_revocation,
        "revocation_reason": str(latest_revocation.get("reason") or ""),
        "revocation_reason_class": str(latest_revocation.get("reason_class") or ""),
    }


def build_live_pilot_launch_authorization_packet_approval_token_audit_summary(
    *,
    approval_token: dict[str, Any] | None = None,
    revoked_approval_tokens: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tok = dict(approval_token or {})
    state = resolve_live_pilot_launch_authorization_packet_approval_token_latest_state(
        approval_token=tok,
        revoked_approval_tokens=revoked_approval_tokens,
    )
    rev_rows = [dict(x) for x in list(revoked_approval_tokens or []) if isinstance(x, dict)]
    class_counts: dict[str, int] = {}
    sev_counts: dict[str, int] = {}
    for row in rev_rows:
        rclass = str(row.get("reason_class") or classify_live_pilot_promotion_ticket_revocation_reason(str(row.get("reason") or "")).get("reason_class") or "other")
        sev = str(row.get("severity") or classify_live_pilot_promotion_ticket_revocation_reason(str(row.get("reason") or "")).get("severity") or "warning")
        class_counts[rclass] = int(class_counts.get(rclass, 0)) + 1
        sev_counts[sev] = int(sev_counts.get(sev, 0)) + 1
    return {
        "generated_unix_ms": int(time.time() * 1000),
        "token_id": str(tok.get("token_id") or ""),
        "authorization_packet_fingerprint_sha256": str(tok.get("authorization_packet_fingerprint_sha256") or ""),
        "token_state": state,
        "revocation_events_total": len(rev_rows),
        "revocation_reason_class_counts": class_counts,
        "revocation_severity_counts": sev_counts,
    }


def write_live_pilot_launch_authorization_packet_approval_token_audit_summary(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        st = dict(report.get("token_state") or {})
        lines = [
            "# Launch Authorization Packet Approval Token Audit",
            "",
            f"- token_id: `{report.get('token_id', '')}`",
            f"- latest_state: `{st.get('latest_state', '')}`",
            f"- revoked: `{bool(st.get('revoked', False))}`",
            f"- revocation_reason_class: `{st.get('revocation_reason_class', '')}`",
            f"- revocation_events_total: `{report.get('revocation_events_total', 0)}`",
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def evaluate_live_launch_guard(
    *,
    adapter_config: dict[str, Any] | None = None,
    enable_live_auto_submit_window: bool = False,
    prelive_go_no_go_report: dict[str, Any] | None = None,
    promotion_ticket: dict[str, Any] | None = None,
    launch_intent_manifest: dict[str, Any] | None = None,
    launch_authorization_packet: dict[str, Any] | None = None,
    launch_authorization_packet_approval_token: dict[str, Any] | None = None,
    requested_mode: str = "",
    requested_risk_profile_preset: str = "",
    require_prelive_go_no_go: bool = False,
    require_bundle_pass: bool = False,
    require_operator_ticket: bool = False,
    require_unused_ticket: bool = False,
    require_unrevoked_ticket: bool = False,
    require_launch_intent: bool = False,
    require_launch_authorization_packet: bool = False,
    require_launch_authorization_packet_binding: bool = False,
    require_launch_authorization_packet_approval_token: bool = False,
    require_unrevoked_launch_authorization_packet_approval_token: bool = False,
    revocation_reason_class_policy_overrides: dict[str, str] | None = None,
    required_ticket_action: str = "approve_live_test",
    required_launch_authorization_packet_approval_action: str = "approve_live_launch_packet",
    max_prelive_age_seconds: float = 3600.0,
    max_launch_intent_age_seconds: float = 1800.0,
    max_launch_authorization_packet_age_seconds: float = 900.0,
    max_launch_authorization_packet_approval_token_age_seconds: float = 900.0,
    consumed_tickets: list[dict[str, Any]] | None = None,
    revoked_tickets: list[dict[str, Any]] | None = None,
    revoked_launch_authorization_packet_approval_tokens: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cfg = dict(adapter_config or {})
    live_network = bool(cfg.get("live_send_network_enabled", False))
    go_no_go = dict(prelive_go_no_go_report or {})
    ticket = dict(promotion_ticket or {})
    intent = dict(launch_intent_manifest or {})
    auth_packet = dict(launch_authorization_packet or {})
    auth_packet_approval_token = dict(launch_authorization_packet_approval_token or {})
    revoked_auth_token_rows = [dict(x) for x in list(revoked_launch_authorization_packet_approval_tokens or []) if isinstance(x, dict)]
    intent_scope = dict(intent.get("scope") or {})
    now_ms = int(time.time() * 1000)
    checks: list[dict[str, Any]] = []
    checks.append({"name": "live_network_enabled", "required": False, "ok": live_network, "actual": live_network})
    checks.append({"name": "live_auto_submit_requested", "required": False, "ok": bool(enable_live_auto_submit_window), "actual": bool(enable_live_auto_submit_window)})
    intent_generated_ms = _to_int_or_none(intent.get("generated_unix_ms"))
    intent_age_ok = False
    if intent_generated_ms is not None:
        intent_age_ok = (now_ms - int(intent_generated_ms)) <= int(max(0.0, float(max_launch_intent_age_seconds)) * 1000.0)
    checks.append({"name": "launch_intent_present", "required": bool(require_launch_intent), "ok": bool(intent), "actual": bool(intent)})
    checks.append({"name": "launch_intent_fresh_enough", "required": bool(require_launch_intent), "ok": intent_age_ok, "actual": (None if intent_generated_ms is None else max(0, now_ms - int(intent_generated_ms)))})
    checks.append(
        {
            "name": "launch_intent_mode_matches_request",
            "required": bool(require_launch_intent),
            "ok": (str(intent_scope.get("mode") or "") == str(requested_mode or "")),
            "actual": str(intent_scope.get("mode") or ""),
        }
    )
    checks.append(
        {
            "name": "launch_intent_risk_profile_matches_request",
            "required": bool(require_launch_intent and str(requested_risk_profile_preset or "").strip()),
            "ok": (str(intent_scope.get("risk_profile_preset") or "") == str(requested_risk_profile_preset or "")),
            "actual": str(intent_scope.get("risk_profile_preset") or ""),
        }
    )
    checks.append(
        {
            "name": "launch_intent_live_submit_flag_matches_request",
            "required": bool(require_launch_intent),
            "ok": bool(intent_scope.get("enable_live_auto_submit_window", False)) == bool(enable_live_auto_submit_window),
            "actual": bool(intent_scope.get("enable_live_auto_submit_window", False)),
        }
    )
    auth_packet_generated_ms = _to_int_or_none(auth_packet.get("generated_unix_ms"))
    auth_packet_age_ok = False
    if auth_packet_generated_ms is not None:
        auth_packet_age_ok = (now_ms - int(auth_packet_generated_ms)) <= int(max(0.0, float(max_launch_authorization_packet_age_seconds)) * 1000.0)
    checks.append(
        {
            "name": "authorization_packet_present",
            "required": bool(require_launch_authorization_packet),
            "ok": bool(auth_packet),
            "actual": bool(auth_packet),
        }
    )
    checks.append(
        {
            "name": "authorization_packet_status_authorized",
            "required": bool(require_launch_authorization_packet),
            "ok": str(auth_packet.get("status") or "") == "authorized",
            "actual": str(auth_packet.get("status") or ""),
        }
    )
    checks.append(
        {
            "name": "authorization_packet_fresh_enough",
            "required": bool(require_launch_authorization_packet),
            "ok": auth_packet_age_ok,
            "actual": (None if auth_packet_generated_ms is None else max(0, now_ms - int(auth_packet_generated_ms))),
        }
    )
    auth_packet_binding = dict(auth_packet.get("binding") or {})
    auth_packet_fp_actual = str(auth_packet.get("packet_fingerprint_sha256") or "")
    auth_packet_fp_expected = ""
    if auth_packet:
        auth_packet_fp_expected = hashlib.sha256(
            json.dumps({k: v for k, v in auth_packet.items() if k != "packet_fingerprint_sha256"}, sort_keys=True).encode("utf-8")
        ).hexdigest()
    checks.append(
        {
            "name": "authorization_packet_fingerprint_valid",
            "required": bool(require_launch_authorization_packet),
            "ok": (bool(auth_packet) and bool(auth_packet_fp_actual) and auth_packet_fp_actual == auth_packet_fp_expected),
            "actual": {
                "packet_fingerprint_sha256": auth_packet_fp_actual,
                "expected_packet_fingerprint_sha256": auth_packet_fp_expected,
            },
        }
    )
    expected_packet_binding = {
        "ticket_id": str(ticket.get("ticket_id") or ""),
        "ticket_fingerprint_sha256": str(ticket.get("ticket_fingerprint_sha256") or ""),
        "intent_id": str(intent.get("intent_id") or ""),
        "intent_scope_hash_sha256": str(intent.get("scope_hash_sha256") or ""),
        "prelive_status": str(go_no_go.get("status") or ""),
    }
    packet_binding_matches_current = (
        bool(auth_packet)
        and str(auth_packet_binding.get("ticket_id") or "") == expected_packet_binding["ticket_id"]
        and str(auth_packet_binding.get("ticket_fingerprint_sha256") or "") == expected_packet_binding["ticket_fingerprint_sha256"]
        and str(auth_packet_binding.get("intent_id") or "") == expected_packet_binding["intent_id"]
        and str(auth_packet_binding.get("intent_scope_hash_sha256") or "") == expected_packet_binding["intent_scope_hash_sha256"]
        and str(auth_packet_binding.get("prelive_status") or "") == expected_packet_binding["prelive_status"]
    )
    checks.append(
        {
            "name": "authorization_packet_bound_to_current_inputs",
            "required": bool(require_launch_authorization_packet and require_launch_authorization_packet_binding),
            "ok": packet_binding_matches_current,
            "actual": {
                "packet_binding": auth_packet_binding,
                "expected_binding": expected_packet_binding,
            },
        }
    )
    auth_token_generated_ms = _to_int_or_none(auth_packet_approval_token.get("issued_unix_ms"))
    auth_token_age_ok = False
    if auth_token_generated_ms is not None:
        auth_token_age_ok = (now_ms - int(auth_token_generated_ms)) <= int(max(0.0, float(max_launch_authorization_packet_approval_token_age_seconds)) * 1000.0)
    auth_token_expires_ms = _to_int_or_none(auth_packet_approval_token.get("expires_unix_ms"))
    auth_token_fp_actual = str(auth_packet_approval_token.get("token_fingerprint_sha256") or "")
    auth_token_fp_expected = ""
    if auth_packet_approval_token:
        auth_token_fp_expected = hashlib.sha256(
            json.dumps({k: v for k, v in auth_packet_approval_token.items() if k != "token_fingerprint_sha256"}, sort_keys=True).encode("utf-8")
        ).hexdigest()
    checks.append(
        {
            "name": "authorization_packet_approval_token_present",
            "required": bool(require_launch_authorization_packet_approval_token),
            "ok": bool(auth_packet_approval_token),
            "actual": bool(auth_packet_approval_token),
        }
    )
    checks.append(
        {
            "name": "authorization_packet_approval_token_action_matches",
            "required": bool(require_launch_authorization_packet_approval_token),
            "ok": str(auth_packet_approval_token.get("approval_action") or "") == str(required_launch_authorization_packet_approval_action or ""),
            "actual": str(auth_packet_approval_token.get("approval_action") or ""),
        }
    )
    checks.append(
        {
            "name": "authorization_packet_approval_token_not_expired",
            "required": bool(require_launch_authorization_packet_approval_token),
            "ok": (auth_token_expires_ms is not None and int(auth_token_expires_ms) >= now_ms),
            "actual": auth_token_expires_ms,
        }
    )
    checks.append(
        {
            "name": "authorization_packet_approval_token_fresh_enough",
            "required": bool(require_launch_authorization_packet_approval_token),
            "ok": auth_token_age_ok,
            "actual": (None if auth_token_generated_ms is None else max(0, now_ms - int(auth_token_generated_ms))),
        }
    )
    checks.append(
        {
            "name": "authorization_packet_approval_token_fingerprint_valid",
            "required": bool(require_launch_authorization_packet_approval_token),
            "ok": (bool(auth_packet_approval_token) and bool(auth_token_fp_actual) and auth_token_fp_actual == auth_token_fp_expected),
            "actual": {
                "token_fingerprint_sha256": auth_token_fp_actual,
                "expected_token_fingerprint_sha256": auth_token_fp_expected,
            },
        }
    )
    checks.append(
        {
            "name": "authorization_packet_approval_token_matches_packet_fingerprint",
            "required": bool(require_launch_authorization_packet_approval_token),
            "ok": (
                bool(auth_packet_approval_token)
                and bool(auth_packet)
                and str(auth_packet_approval_token.get("authorization_packet_fingerprint_sha256") or "") == str(auth_packet.get("packet_fingerprint_sha256") or "")
            ),
            "actual": {
                "token_authorization_packet_fingerprint_sha256": str(auth_packet_approval_token.get("authorization_packet_fingerprint_sha256") or ""),
                "packet_fingerprint_sha256": str(auth_packet.get("packet_fingerprint_sha256") or ""),
            },
        }
    )
    auth_token_id = str(auth_packet_approval_token.get("token_id") or "")
    auth_token_fp = str(auth_packet_approval_token.get("token_fingerprint_sha256") or "")
    auth_token_revoked = any(
        (auth_token_id and str(r.get("token_id") or "") == auth_token_id)
        or (auth_token_fp and str(r.get("token_fingerprint_sha256") or "") == auth_token_fp)
        for r in revoked_auth_token_rows
    )
    checks.append(
        {
            "name": "authorization_packet_approval_token_unrevoked",
            "required": bool(require_launch_authorization_packet_approval_token and require_unrevoked_launch_authorization_packet_approval_token),
            "ok": (not auth_token_revoked),
            "actual": auth_token_revoked,
        }
    )

    prelive_generated_ms = _to_int_or_none(go_no_go.get("generated_unix_ms"))
    prelive_age_ok = False
    if prelive_generated_ms is not None:
        prelive_age_ok = (now_ms - int(prelive_generated_ms)) <= int(max(0.0, float(max_prelive_age_seconds)) * 1000.0)
    checks.append(
        {
            "name": "prelive_report_present",
            "required": bool(require_prelive_go_no_go),
            "ok": bool(go_no_go),
            "actual": bool(go_no_go),
        }
    )
    checks.append(
        {
            "name": "prelive_status_go",
            "required": bool(require_prelive_go_no_go),
            "ok": str(go_no_go.get("status") or "") == "go",
            "actual": str(go_no_go.get("status") or ""),
        }
    )
    checks.append(
        {
            "name": "prelive_fresh_enough",
            "required": bool(require_prelive_go_no_go),
            "ok": prelive_age_ok,
            "actual": (None if prelive_generated_ms is None else max(0, now_ms - int(prelive_generated_ms))),
        }
    )
    if require_bundle_pass:
        checks.append(
            {
                "name": "prelive_bundle_verification_pass",
                "required": True,
                "ok": str(go_no_go.get("bundle_verification_status") or "") == "pass",
                "actual": str(go_no_go.get("bundle_verification_status") or ""),
            }
        )
    ticket_expires = _to_int_or_none(ticket.get("expires_unix_ms"))
    consumed_rows = [dict(x) for x in list(consumed_tickets or []) if isinstance(x, dict)]
    revoked_rows = [dict(x) for x in list(revoked_tickets or []) if isinstance(x, dict)]
    ticket_id = str(ticket.get("ticket_id") or "")
    ticket_fp = str(ticket.get("ticket_fingerprint_sha256") or "")
    ticket_consumed = any(
        (ticket_id and str(r.get("ticket_id") or "") == ticket_id)
        or (ticket_fp and str(r.get("ticket_fingerprint_sha256") or "") == ticket_fp)
        for r in consumed_rows
    )
    ticket_revoked = any(
        (ticket_id and str(r.get("ticket_id") or "") == ticket_id)
        or (ticket_fp and str(r.get("ticket_fingerprint_sha256") or "") == ticket_fp)
        for r in revoked_rows
    )
    matching_revocations = [
        dict(r)
        for r in revoked_rows
        if (
            (ticket_id and str(r.get("ticket_id") or "") == ticket_id)
            or (ticket_fp and str(r.get("ticket_fingerprint_sha256") or "") == ticket_fp)
        )
    ]
    matching_revocations.sort(key=lambda r: int(_to_int_or_none(r.get("ts_unix_ms")) or 0), reverse=True)
    latest_revocation = dict(matching_revocations[0]) if matching_revocations else {}
    latest_revocation_reason = str(latest_revocation.get("reason") or "")
    latest_revocation_reason_class = str(latest_revocation.get("reason_class") or "")
    if latest_revocation and not latest_revocation_reason_class:
        latest_revocation_reason_class = str(classify_live_pilot_promotion_ticket_revocation_reason(latest_revocation_reason).get("reason_class") or "other")
    revocation_policy_overrides = {
        str(k).strip().lower(): str(v).strip().lower()
        for k, v in dict(revocation_reason_class_policy_overrides or {}).items()
        if str(k).strip() and str(v).strip().lower() in {"allow", "block"}
    }
    ticket_revocation_policy_action = "block"
    if latest_revocation:
        ticket_revocation_policy_action = str(revocation_policy_overrides.get(str(latest_revocation_reason_class or "").lower()) or "block")
    ticket_effectively_revoked = bool(ticket_revoked and ticket_revocation_policy_action != "allow")
    checks.append({"name": "ticket_present", "required": bool(require_operator_ticket), "ok": bool(ticket), "actual": bool(ticket)})
    checks.append(
        {
            "name": "ticket_action_matches",
            "required": bool(require_operator_ticket),
            "ok": str(ticket.get("approval_action") or "") == str(required_ticket_action or ""),
            "actual": str(ticket.get("approval_action") or ""),
        }
    )
    checks.append(
        {
            "name": "ticket_not_expired",
            "required": bool(require_operator_ticket),
            "ok": (ticket_expires is not None and int(ticket_expires) >= now_ms),
            "actual": ticket_expires,
        }
    )
    checks.append(
        {
            "name": "ticket_unused",
            "required": bool(require_operator_ticket and require_unused_ticket),
            "ok": (not ticket_consumed),
            "actual": ticket_consumed,
        }
    )
    checks.append(
        {
            "name": "ticket_unrevoked",
            "required": bool(require_operator_ticket and require_unrevoked_ticket),
            "ok": (not ticket_effectively_revoked),
            "actual": {
                "ticket_revoked": ticket_revoked,
                "effective_revoked": ticket_effectively_revoked,
                "revocation_policy_action": ticket_revocation_policy_action,
                "revocation_reason_class": latest_revocation_reason_class,
                "revocation_reason": latest_revocation_reason,
            },
        }
    )
    checks.append(
        {
            "name": "ticket_bound_to_launch_intent",
            "required": bool(require_operator_ticket and require_launch_intent),
            "ok": (
                bool(ticket)
                and bool(intent)
                and str(ticket.get("intent_scope_hash_sha256") or "") == str(intent.get("scope_hash_sha256") or "")
            ),
            "actual": {
                "ticket_intent_scope_hash_sha256": str(ticket.get("intent_scope_hash_sha256") or ""),
                "launch_intent_scope_hash_sha256": str(intent.get("scope_hash_sha256") or ""),
            },
        }
    )

    required_failed = [c["name"] for c in checks if bool(c.get("required", False)) and not bool(c.get("ok", False))]
    live_launch_requested = bool(live_network and enable_live_auto_submit_window)
    allowed = (not live_launch_requested) or (len(required_failed) == 0)
    status = "allow" if allowed else "block"
    return {
        "generated_unix_ms": now_ms,
        "status": status,
        "live_launch_requested": live_launch_requested,
        "required_failed_checks": required_failed,
        "checks": checks,
        "ticket_consumed": ticket_consumed,
        "ticket_revoked": ticket_revoked,
        "ticket_effectively_revoked": ticket_effectively_revoked,
        "ticket_revocation_reason": latest_revocation_reason,
        "ticket_revocation_reason_class": latest_revocation_reason_class,
        "ticket_revocation_policy_action": ticket_revocation_policy_action,
        "authorization_packet_status": str(auth_packet.get("status") or ""),
        "authorization_packet_fingerprint_valid": bool(auth_packet and auth_packet_fp_actual and auth_packet_fp_actual == auth_packet_fp_expected),
        "authorization_packet_approval_token_id": str(auth_packet_approval_token.get("token_id") or ""),
        "authorization_packet_approval_token_revoked": auth_token_revoked,
        "summary": ("live_launch_guard_allow" if allowed else f"live_launch_guard_block:{','.join(required_failed)}"),
    }


def build_live_pilot_ticket_state_consistency_report(
    *,
    promotion_ticket: dict[str, Any] | None = None,
    launch_intent_manifest: dict[str, Any] | None = None,
    prelive_go_no_go_report: dict[str, Any] | None = None,
    consumed_tickets: list[dict[str, Any]] | None = None,
    revoked_tickets: list[dict[str, Any]] | None = None,
    revocation_reason_class_policy_overrides: dict[str, str] | None = None,
    launch_guard_report: dict[str, Any] | None = None,
    max_prelive_age_seconds: float = 3600.0,
    max_launch_intent_age_seconds: float = 1800.0,
) -> dict[str, Any]:
    ticket = dict(promotion_ticket or {})
    intent = dict(launch_intent_manifest or {})
    go_no_go = dict(prelive_go_no_go_report or {})
    guard = dict(launch_guard_report or {})
    now_ms = int(time.time() * 1000)
    ticket_state = resolve_live_pilot_promotion_ticket_latest_state(
        ticket=ticket,
        consumed_tickets=consumed_tickets,
        revoked_tickets=revoked_tickets,
        revocation_reason_class_policy_overrides=revocation_reason_class_policy_overrides,
    )
    intent_scope = dict(intent.get("scope") or {})
    prelive_age_ms = _to_int_or_none(go_no_go.get("generated_unix_ms"))
    intent_age_ms = _to_int_or_none(intent.get("generated_unix_ms"))
    checks: list[dict[str, Any]] = []
    checks.append({"name": "ticket_present", "ok": bool(ticket), "required": True, "actual": bool(ticket)})
    checks.append({"name": "intent_present", "ok": bool(intent), "required": True, "actual": bool(intent)})
    checks.append(
        {
            "name": "ticket_intent_scope_hash_matches_intent",
            "ok": bool(ticket)
            and bool(intent)
            and str(ticket.get("intent_scope_hash_sha256") or "") == str(intent.get("scope_hash_sha256") or ""),
            "required": True,
            "actual": {
                "ticket": str(ticket.get("intent_scope_hash_sha256") or ""),
                "intent": str(intent.get("scope_hash_sha256") or ""),
            },
        }
    )
    checks.append(
        {
            "name": "ticket_intent_id_matches_intent",
            "ok": bool(ticket) and bool(intent) and str(ticket.get("intent_id") or "") == str(intent.get("intent_id") or ""),
            "required": True,
            "actual": {"ticket": str(ticket.get("intent_id") or ""), "intent": str(intent.get("intent_id") or "")},
        }
    )
    checks.append(
        {
            "name": "ticket_not_expired",
            "ok": (_to_int_or_none(ticket.get("expires_unix_ms")) or 0) >= now_ms,
            "required": True,
            "actual": _to_int_or_none(ticket.get("expires_unix_ms")),
        }
    )
    checks.append(
        {
            "name": "prelive_status_go",
            "ok": str(go_no_go.get("status") or "") == "go",
            "required": False,
            "actual": str(go_no_go.get("status") or ""),
        }
    )
    checks.append(
        {
            "name": "prelive_fresh_enough",
            "ok": (prelive_age_ms is not None and (now_ms - int(prelive_age_ms)) <= int(max(0.0, float(max_prelive_age_seconds)) * 1000.0)),
            "required": False,
            "actual": (None if prelive_age_ms is None else max(0, now_ms - int(prelive_age_ms))),
        }
    )
    checks.append(
        {
            "name": "launch_intent_fresh_enough",
            "ok": (intent_age_ms is not None and (now_ms - int(intent_age_ms)) <= int(max(0.0, float(max_launch_intent_age_seconds)) * 1000.0)),
            "required": False,
            "actual": (None if intent_age_ms is None else max(0, now_ms - int(intent_age_ms))),
        }
    )
    checks.append(
        {
            "name": "ticket_state_not_effectively_revoked",
            "ok": not bool(ticket_state.get("effective_revoked", False)),
            "required": True,
            "actual": {
                "latest_state": str(ticket_state.get("latest_state") or ""),
                "effective_revoked": bool(ticket_state.get("effective_revoked", False)),
                "revocation_reason_class": str(ticket_state.get("revocation_reason_class") or ""),
                "revocation_policy_action": str(ticket_state.get("revocation_policy_action") or ""),
            },
        }
    )
    checks.append(
        {
            "name": "launch_guard_consistent_with_ticket_state",
            "ok": (
                (not guard)
                or (
                    bool(guard.get("ticket_effectively_revoked", False)) == bool(ticket_state.get("effective_revoked", False))
                    and str(guard.get("ticket_revocation_policy_action") or "") == str(ticket_state.get("revocation_policy_action") or "")
                )
            ),
            "required": False,
            "actual": {
                "guard_ticket_effectively_revoked": bool(guard.get("ticket_effectively_revoked", False)) if guard else None,
                "resolved_ticket_effectively_revoked": bool(ticket_state.get("effective_revoked", False)),
                "guard_policy_action": str(guard.get("ticket_revocation_policy_action") or "") if guard else "",
                "resolved_policy_action": str(ticket_state.get("revocation_policy_action") or ""),
            },
        }
    )
    failed_required = [c["name"] for c in checks if bool(c.get("required", False)) and not bool(c.get("ok", False))]
    status = "pass" if not failed_required else "fail"
    return {
        "generated_unix_ms": now_ms,
        "status": status,
        "failed_required_checks": failed_required,
        "ticket_id": str(ticket.get("ticket_id") or ""),
        "intent_id": str(intent.get("intent_id") or ""),
        "ticket_state": ticket_state,
        "scope": {
            "mode": str(intent_scope.get("mode") or ""),
            "risk_profile_preset": str(intent_scope.get("risk_profile_preset") or ""),
            "live_send_network_enabled": bool(intent_scope.get("live_send_network_enabled", False)),
            "enable_live_auto_submit_window": bool(intent_scope.get("enable_live_auto_submit_window", False)),
        },
        "checks": checks,
    }


def write_live_pilot_ticket_state_consistency_report(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = [
            "# Live Pilot Ticket State Consistency Report",
            "",
            f"- status: `{report.get('status', '')}`",
            f"- ticket_id: `{report.get('ticket_id', '')}`",
            f"- intent_id: `{report.get('intent_id', '')}`",
            f"- failed_required_checks: `{', '.join(list(report.get('failed_required_checks', []) or [])) or '-'}`",
            "",
            "## Checks",
            "",
        ]
        for c in [dict(x) for x in list(report.get("checks") or []) if isinstance(x, dict)]:
            lines.append(f"- {c.get('name','')}: `{'pass' if c.get('ok') else 'fail'}` required=`{bool(c.get('required', False))}` actual=`{c.get('actual')}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_promotion_ticket_lifecycle_timeline(
    *,
    promotion_ticket: dict[str, Any] | None = None,
    consumed_tickets: list[dict[str, Any]] | None = None,
    revoked_tickets: list[dict[str, Any]] | None = None,
    launch_guard_report: dict[str, Any] | None = None,
    revocation_reason_class_policy_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    ticket = dict(promotion_ticket or {})
    consumed_rows = [dict(x) for x in list(consumed_tickets or []) if isinstance(x, dict)]
    revoked_rows = [dict(x) for x in list(revoked_tickets or []) if isinstance(x, dict)]
    guard = dict(launch_guard_report or {})
    state = resolve_live_pilot_promotion_ticket_latest_state(
        ticket=ticket,
        consumed_tickets=consumed_rows,
        revoked_tickets=revoked_rows,
        revocation_reason_class_policy_overrides=revocation_reason_class_policy_overrides,
    )
    events: list[dict[str, Any]] = []
    if ticket:
        events.append(
            {
                "event_type": "ticket_issued",
                "ts_unix_ms": int(_to_int_or_none(ticket.get("issued_unix_ms")) or 0),
                "details": {
                    "ticket_id": str(ticket.get("ticket_id") or ""),
                    "approval_action": str(ticket.get("approval_action") or ""),
                    "operator_id": str(ticket.get("operator_id") or ""),
                    "expires_unix_ms": _to_int_or_none(ticket.get("expires_unix_ms")),
                },
            }
        )
    for row in consumed_rows:
        events.append(
            {
                "event_type": "ticket_consumed",
                "ts_unix_ms": int(_to_int_or_none(row.get("ts_unix_ms")) or 0),
                "details": {
                    "reason": str(row.get("reason") or ""),
                    "operator_id": str(row.get("operator_id") or ""),
                    "approval_action": str(row.get("approval_action") or ""),
                },
            }
        )
    for row in revoked_rows:
        reason_meta = classify_live_pilot_promotion_ticket_revocation_reason(str(row.get("reason") or ""))
        events.append(
            {
                "event_type": "ticket_revoked",
                "ts_unix_ms": int(_to_int_or_none(row.get("ts_unix_ms")) or 0),
                "details": {
                    "reason": str(row.get("reason") or ""),
                    "reason_class": str(row.get("reason_class") or reason_meta.get("reason_class") or ""),
                    "severity": str(row.get("severity") or reason_meta.get("severity") or ""),
                    "operator_id": str(row.get("operator_id") or ""),
                },
            }
        )
    if guard:
        events.append(
            {
                "event_type": "launch_guard_evaluated",
                "ts_unix_ms": int(time.time() * 1000),
                "details": {
                    "status": str(guard.get("status") or ""),
                    "live_launch_requested": bool(guard.get("live_launch_requested", False)),
                    "required_failed_checks": list(guard.get("required_failed_checks", []) or []),
                    "ticket_revoked": bool(guard.get("ticket_revoked", False)),
                    "ticket_effectively_revoked": bool(guard.get("ticket_effectively_revoked", False)),
                    "ticket_revocation_policy_action": str(guard.get("ticket_revocation_policy_action") or ""),
                },
            }
        )
    events.sort(key=lambda e: (int(e.get("ts_unix_ms") or 0), str(e.get("event_type") or "")))
    return {
        "generated_unix_ms": int(time.time() * 1000),
        "ticket_id": str(ticket.get("ticket_id") or ""),
        "ticket_fingerprint_sha256": str(ticket.get("ticket_fingerprint_sha256") or ""),
        "event_count": len(events),
        "latest_state": str(state.get("latest_state") or ""),
        "ticket_state": state,
        "events": events,
    }


def write_live_pilot_promotion_ticket_lifecycle_timeline(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = [
            "# Live Pilot Promotion Ticket Lifecycle Timeline",
            "",
            f"- ticket_id: `{report.get('ticket_id', '')}`",
            f"- latest_state: `{report.get('latest_state', '')}`",
            f"- event_count: `{report.get('event_count', 0)}`",
            "",
            "## Events",
            "",
        ]
        for e in [dict(x) for x in list(report.get("events") or []) if isinstance(x, dict)]:
            lines.append(f"- {e.get('event_type','')}: ts=`{e.get('ts_unix_ms',0)}` details=`{json.dumps(e.get('details', {}), sort_keys=True)}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_launch_authorization_packet(
    *,
    prelive_go_no_go_report: dict[str, Any] | None = None,
    promotion_ticket: dict[str, Any] | None = None,
    launch_intent_manifest: dict[str, Any] | None = None,
    live_launch_guard_report: dict[str, Any] | None = None,
    ticket_state_consistency_report: dict[str, Any] | None = None,
    revocation_audit_report: dict[str, Any] | None = None,
    ticket_lifecycle_timeline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    go_no_go = dict(prelive_go_no_go_report or {})
    ticket = dict(promotion_ticket or {})
    intent = dict(launch_intent_manifest or {})
    guard = dict(live_launch_guard_report or {})
    consistency = dict(ticket_state_consistency_report or {})
    audit = dict(revocation_audit_report or {})
    timeline = dict(ticket_lifecycle_timeline or {})
    checks = [
        {"name": "prelive_go", "required": True, "ok": str(go_no_go.get("status") or "") == "go", "actual": str(go_no_go.get("status") or "")},
        {"name": "ticket_present", "required": True, "ok": bool(ticket), "actual": bool(ticket)},
        {"name": "launch_intent_present", "required": True, "ok": bool(intent), "actual": bool(intent)},
        {"name": "live_launch_guard_allow", "required": True, "ok": str(guard.get("status") or "") == "allow", "actual": str(guard.get("status") or "")},
        {"name": "ticket_state_consistency_pass", "required": True, "ok": str(consistency.get("status") or "") == "pass", "actual": str(consistency.get("status") or "")},
        {
            "name": "revocation_not_effective",
            "required": True,
            "ok": not bool(((audit.get("ticket_state") or {}).get("effective_revoked", False))),
            "actual": bool(((audit.get("ticket_state") or {}).get("effective_revoked", False))),
        },
    ]
    failed_required = [c["name"] for c in checks if bool(c.get("required", False)) and not bool(c.get("ok", False))]
    status = "authorized" if not failed_required else "blocked"
    out = {
        "generated_unix_ms": int(time.time() * 1000),
        "status": status,
        "failed_required_checks": failed_required,
        "ticket_id": str(ticket.get("ticket_id") or ""),
        "intent_id": str(intent.get("intent_id") or ""),
        "summary": {
            "prelive_status": str(go_no_go.get("status") or ""),
            "guard_status": str(guard.get("status") or ""),
            "guard_required_failed_checks": list(guard.get("required_failed_checks", []) or []),
            "ticket_consistency_status": str(consistency.get("status") or ""),
            "ticket_latest_state": str((timeline.get("ticket_state") or {}).get("latest_state") or (audit.get("ticket_state") or {}).get("latest_state") or ""),
            "ticket_effectively_revoked": bool(((audit.get("ticket_state") or {}).get("effective_revoked", False))),
        },
        "checks": checks,
        "artifact_refs": {
            "prelive_go_no_go_report_present": bool(go_no_go),
            "promotion_ticket_present": bool(ticket),
            "launch_intent_manifest_present": bool(intent),
            "live_launch_guard_report_present": bool(guard),
            "ticket_state_consistency_report_present": bool(consistency),
            "revocation_audit_report_present": bool(audit),
            "ticket_lifecycle_timeline_present": bool(timeline),
        },
        "source_timestamps_unix_ms": {
            "prelive_go_no_go_report": _to_int_or_none(go_no_go.get("generated_unix_ms")),
            "promotion_ticket_issued": _to_int_or_none(ticket.get("issued_unix_ms")),
            "launch_intent_manifest": _to_int_or_none(intent.get("generated_unix_ms")),
            "live_launch_guard_report": _to_int_or_none(guard.get("generated_unix_ms")),
            "ticket_state_consistency_report": _to_int_or_none(consistency.get("generated_unix_ms")),
            "revocation_audit_report": _to_int_or_none(audit.get("generated_unix_ms")),
            "ticket_lifecycle_timeline": _to_int_or_none(timeline.get("generated_unix_ms")),
        },
    }
    out["binding"] = {
        "ticket_id": str(ticket.get("ticket_id") or ""),
        "ticket_fingerprint_sha256": str(ticket.get("ticket_fingerprint_sha256") or ""),
        "intent_id": str(intent.get("intent_id") or ""),
        "intent_scope_hash_sha256": str(intent.get("scope_hash_sha256") or ""),
        "prelive_status": str(go_no_go.get("status") or ""),
        "guard_status": str(guard.get("status") or ""),
        "guard_required_failed_checks": list(guard.get("required_failed_checks", []) or []),
    }
    fp_src = json.dumps({k: v for k, v in out.items() if k != "packet_fingerprint_sha256"}, sort_keys=True)
    out["packet_fingerprint_sha256"] = hashlib.sha256(fp_src.encode("utf-8")).hexdigest()
    return out


def write_live_pilot_launch_authorization_packet(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        s = dict(report.get("summary") or {})
        lines = [
            "# Live Pilot Launch Authorization Packet",
            "",
            f"- status: `{report.get('status', '')}`",
            f"- ticket_id: `{report.get('ticket_id', '')}`",
            f"- intent_id: `{report.get('intent_id', '')}`",
            f"- packet_fingerprint_sha256: `{report.get('packet_fingerprint_sha256', '')}`",
            f"- failed_required_checks: `{', '.join(list(report.get('failed_required_checks', []) or [])) or '-'}`",
            "",
            "## Summary",
            "",
            f"- prelive_status: `{s.get('prelive_status', '')}`",
            f"- guard_status: `{s.get('guard_status', '')}`",
            f"- ticket_consistency_status: `{s.get('ticket_consistency_status', '')}`",
            f"- ticket_latest_state: `{s.get('ticket_latest_state', '')}`",
            f"- ticket_effectively_revoked: `{bool(s.get('ticket_effectively_revoked', False))}`",
            "",
            "## Checks",
            "",
        ]
        for c in [dict(x) for x in list(report.get("checks") or []) if isinstance(x, dict)]:
            lines.append(f"- {c.get('name','')}: `{'pass' if c.get('ok') else 'fail'}` required=`{bool(c.get('required', False))}` actual=`{c.get('actual')}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_launch_authorization_packet_approval_token(
    *,
    launch_authorization_packet: dict[str, Any] | None = None,
    operator_id: str = "",
    approval_action: str = "approve_live_launch_packet",
    expires_in_seconds: float = 900.0,
) -> dict[str, Any]:
    packet = dict(launch_authorization_packet or {})
    now_ms = int(time.time() * 1000)
    payload = {
        "issued_unix_ms": now_ms,
        "expires_unix_ms": now_ms + int(max(0.0, float(expires_in_seconds)) * 1000.0),
        "operator_id": str(operator_id or ""),
        "approval_action": str(approval_action or "approve_live_launch_packet"),
        "authorization_packet_status": str(packet.get("status") or ""),
        "authorization_packet_fingerprint_sha256": str(packet.get("packet_fingerprint_sha256") or ""),
        "ticket_id": str(packet.get("ticket_id") or ""),
        "intent_id": str(packet.get("intent_id") or ""),
    }
    token_id_seed = json.dumps(payload, sort_keys=True)
    payload["token_id"] = "lpa_" + hashlib.sha256(token_id_seed.encode("utf-8")).hexdigest()[:16]
    token_fp_src = json.dumps(payload, sort_keys=True)
    payload["token_fingerprint_sha256"] = hashlib.sha256(token_fp_src.encode("utf-8")).hexdigest()
    return payload


def write_live_pilot_launch_authorization_packet_approval_token(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = [
            "# Live Pilot Launch Authorization Packet Approval Token",
            "",
            f"- token_id: `{report.get('token_id', '')}`",
            f"- operator_id: `{report.get('operator_id', '')}`",
            f"- approval_action: `{report.get('approval_action', '')}`",
            f"- authorization_packet_fingerprint_sha256: `{report.get('authorization_packet_fingerprint_sha256', '')}`",
            f"- expires_unix_ms: `{report.get('expires_unix_ms', 0)}`",
            f"- token_fingerprint_sha256: `{report.get('token_fingerprint_sha256', '')}`",
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_launch_authorization_freshness_envelope(
    *,
    launch_authorization_packet: dict[str, Any] | None = None,
    max_age_seconds_by_source: dict[str, float] | None = None,
    max_packet_age_seconds: float = 900.0,
) -> dict[str, Any]:
    packet = dict(launch_authorization_packet or {})
    now_ms = int(time.time() * 1000)
    source_ts = dict(packet.get("source_timestamps_unix_ms") or {})
    age_limits: dict[str, float] = {
        "prelive_go_no_go_report": 3600.0,
        "promotion_ticket_issued": 7200.0,
        "launch_intent_manifest": 1800.0,
        "live_launch_guard_report": 900.0,
        "ticket_state_consistency_report": 900.0,
        "revocation_audit_report": 900.0,
        "ticket_lifecycle_timeline": 900.0,
    }
    for k, v in dict(max_age_seconds_by_source or {}).items():
        try:
            age_limits[str(k)] = float(v)
        except Exception:
            continue
    checks: list[dict[str, Any]] = []
    packet_generated_ms = _to_int_or_none(packet.get("generated_unix_ms"))
    packet_age_ok = False
    if packet_generated_ms is not None:
        packet_age_ok = (now_ms - int(packet_generated_ms)) <= int(max(0.0, float(max_packet_age_seconds)) * 1000.0)
    checks.append({"name": "authorization_packet_present", "required": True, "ok": bool(packet), "actual": bool(packet)})
    checks.append(
        {
            "name": "authorization_packet_status_authorized",
            "required": True,
            "ok": str(packet.get("status") or "") == "authorized",
            "actual": str(packet.get("status") or ""),
        }
    )
    checks.append(
        {
            "name": "authorization_packet_fresh_enough",
            "required": True,
            "ok": packet_age_ok,
            "actual": (None if packet_generated_ms is None else max(0, now_ms - int(packet_generated_ms))),
        }
    )
    stale_sources: list[str] = []
    missing_sources: list[str] = []
    for name in sorted(age_limits.keys()):
        ts_val = _to_int_or_none(source_ts.get(name))
        max_age = float(age_limits.get(name, 0.0) or 0.0)
        if ts_val is None:
            missing_sources.append(name)
            checks.append(
                {
                    "name": f"source_fresh:{name}",
                    "required": False,
                    "ok": False,
                    "actual": {"present": False, "age_ms": None, "max_age_seconds": max_age},
                }
            )
            continue
        age_ms = max(0, now_ms - int(ts_val))
        ok = age_ms <= int(max(0.0, max_age) * 1000.0)
        if not ok:
            stale_sources.append(name)
        checks.append(
            {
                "name": f"source_fresh:{name}",
                "required": False,
                "ok": ok,
                "actual": {"present": True, "age_ms": age_ms, "max_age_seconds": max_age},
            }
        )
    failed_required = [c["name"] for c in checks if bool(c.get("required", False)) and not bool(c.get("ok", False))]
    return {
        "generated_unix_ms": now_ms,
        "status": ("pass" if not failed_required else "fail"),
        "failed_required_checks": failed_required,
        "authorization_packet_status": str(packet.get("status") or ""),
        "stale_sources": stale_sources,
        "missing_sources": missing_sources,
        "checks": checks,
    }


def write_live_pilot_launch_authorization_freshness_envelope(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = [
            "# Live Pilot Launch Authorization Freshness Envelope",
            "",
            f"- status: `{report.get('status', '')}`",
            f"- authorization_packet_status: `{report.get('authorization_packet_status', '')}`",
            f"- failed_required_checks: `{', '.join(list(report.get('failed_required_checks', []) or [])) or '-'}`",
            f"- stale_sources: `{', '.join(list(report.get('stale_sources', []) or [])) or '-'}`",
            f"- missing_sources: `{', '.join(list(report.get('missing_sources', []) or [])) or '-'}`",
            "",
            "## Checks",
            "",
        ]
        for c in [dict(x) for x in list(report.get("checks") or []) if isinstance(x, dict)]:
            lines.append(f"- {c.get('name','')}: `{'pass' if c.get('ok') else 'fail'}` required=`{bool(c.get('required', False))}` actual=`{c.get('actual')}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def build_live_pilot_launch_authorization_chain_report(
    *,
    launch_authorization_packet: dict[str, Any] | None = None,
    launch_authorization_packet_approval_token: dict[str, Any] | None = None,
    launch_authorization_packet_approval_token_audit_summary: dict[str, Any] | None = None,
    promotion_ticket_revocation_audit_summary: dict[str, Any] | None = None,
    live_launch_guard_report: dict[str, Any] | None = None,
    launch_authorization_freshness_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packet = dict(launch_authorization_packet or {})
    tok = dict(launch_authorization_packet_approval_token or {})
    tok_audit = dict(launch_authorization_packet_approval_token_audit_summary or {})
    ticket_audit = dict(promotion_ticket_revocation_audit_summary or {})
    guard = dict(live_launch_guard_report or {})
    fresh = dict(launch_authorization_freshness_envelope or {})
    checks = [
        {"name": "packet_present", "required": True, "ok": bool(packet), "actual": bool(packet)},
        {"name": "packet_status_authorized", "required": True, "ok": str(packet.get("status") or "") == "authorized", "actual": str(packet.get("status") or "")},
        {"name": "packet_approval_token_present", "required": True, "ok": bool(tok), "actual": bool(tok)},
        {
            "name": "packet_approval_token_matches_packet",
            "required": True,
            "ok": bool(tok)
            and bool(packet)
            and str(tok.get("authorization_packet_fingerprint_sha256") or "") == str(packet.get("packet_fingerprint_sha256") or ""),
            "actual": {
                "token_authorization_packet_fingerprint_sha256": str(tok.get("authorization_packet_fingerprint_sha256") or ""),
                "packet_fingerprint_sha256": str(packet.get("packet_fingerprint_sha256") or ""),
            },
        },
        {"name": "packet_approval_token_not_revoked", "required": True, "ok": not bool(((tok_audit.get("token_state") or {}).get("revoked", False))), "actual": bool(((tok_audit.get("token_state") or {}).get("revoked", False)))},
        {"name": "packet_freshness_envelope_pass", "required": False, "ok": str(fresh.get("status") or "") == "pass", "actual": str(fresh.get("status") or "")},
        {"name": "guard_allow", "required": False, "ok": str(guard.get("status") or "") == "allow", "actual": str(guard.get("status") or "")},
    ]
    failed_required = [c["name"] for c in checks if bool(c.get("required", False)) and not bool(c.get("ok", False))]
    status = "ready" if not failed_required else "blocked"
    return {
        "generated_unix_ms": int(time.time() * 1000),
        "status": status,
        "failed_required_checks": failed_required,
        "summary": {
            "packet_status": str(packet.get("status") or ""),
            "packet_fingerprint_sha256": str(packet.get("packet_fingerprint_sha256") or ""),
            "packet_approval_token_id": str(tok.get("token_id") or ""),
            "packet_approval_token_revoked": bool(((tok_audit.get("token_state") or {}).get("revoked", False))),
            "promotion_ticket_effective_revoked": bool(((ticket_audit.get("ticket_state") or {}).get("effective_revoked", False))),
            "guard_status": str(guard.get("status") or ""),
            "guard_required_failed_checks": list(guard.get("required_failed_checks", []) or []),
            "freshness_status": str(fresh.get("status") or ""),
        },
        "checks": checks,
    }


def write_live_pilot_launch_authorization_chain_report(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        s = dict(report.get("summary") or {})
        lines = [
            "# Live Pilot Launch Authorization Chain Report",
            "",
            f"- status: `{report.get('status', '')}`",
            f"- failed_required_checks: `{', '.join(list(report.get('failed_required_checks', []) or [])) or '-'}`",
            f"- packet_status: `{s.get('packet_status', '')}`",
            f"- packet_approval_token_id: `{s.get('packet_approval_token_id', '')}`",
            f"- packet_approval_token_revoked: `{bool(s.get('packet_approval_token_revoked', False))}`",
            f"- guard_status: `{s.get('guard_status', '')}`",
            "",
            "## Checks",
            "",
        ]
        for c in [dict(x) for x in list(report.get("checks") or []) if isinstance(x, dict)]:
            lines.append(f"- {c.get('name','')}: `{'pass' if c.get('ok') else 'fail'}` required=`{bool(c.get('required', False))}` actual=`{c.get('actual')}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def write_live_launch_guard_report(report: dict[str, Any], path_str: str) -> None:
    p = Path(path_str)
    if p.suffix.lower() in {".md", ".markdown"}:
        lines = [
            "# Live Launch Guard Report",
            "",
            f"- status: `{report.get('status', '')}`",
            f"- live_launch_requested: `{bool(report.get('live_launch_requested', False))}`",
            f"- required_failed_checks: `{', '.join(list(report.get('required_failed_checks', []) or [])) or '-'}`",
            "",
            "## Checks",
            "",
        ]
        for c in [dict(x) for x in list(report.get("checks") or []) if isinstance(x, dict)]:
            lines.append(f"- {c.get('name','')}: `{'pass' if c.get('ok') else 'fail'}` required=`{bool(c.get('required',False))}` actual=`{c.get('actual')}`")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def _path_with_inserted_suffix(path_str: str, suffix: str) -> str:
    if not str(path_str or "").strip():
        return ""
    p = Path(path_str)
    if p.suffix:
        return str(p.with_name(f"{p.stem}{suffix}{p.suffix}"))
    return str(p.with_name(f"{p.name}{suffix}"))


def _write_campaign_schedule_report(report: dict[str, Any], path_str: str) -> None:
    path = Path(path_str)
    if path.suffix.lower() in {".md", ".markdown"}:
        summary = dict(report.get("schedule_summary") or {})
        daily = dict(report.get("daily_operator_report") or {})
        op = dict(daily.get("operator_decision_summary") or {})
        lines = [
            "# Live Pilot Campaign Schedule Report",
            "",
            f"- schedule_id: `{summary.get('schedule_id', '')}`",
            f"- target_sessions: `{summary.get('target_sessions', 0)}`",
            f"- completed_sessions: `{summary.get('completed_sessions', 0)}`",
            f"- stop_reason: `{summary.get('stop_reason', '') or '-'}`",
            f"- timebox_elapsed: `{bool(summary.get('timebox_elapsed', False))}`",
            "",
            "## Daily Operator Decision",
            "",
            f"- recommended_action: `{op.get('recommended_action', '')}`",
            f"- decision_status: `{op.get('decision_status', '')}`",
            f"- promotion_ready_today: `{bool(op.get('promotion_ready_today', False))}`",
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")


def run_live_pilot_campaign_schedule(
    *,
    target_sessions: int,
    run_campaign_fn,
    schedule_id: str | None = None,
    session_interval_seconds: float = 0.0,
    schedule_max_duration_seconds: float = 0.0,
    schedule_state_json_path: str = "",
    schedule_report_path: str = "",
    resume_schedule: bool = False,
    stop_on_campaign_stop_reason: bool = False,
    daily_operator_report_path: str = "",
    daily_operator_date_label: str = "",
    recommendation_config: dict[str, Any] | None = None,
    resume_state_strict: bool = False,
    now_fn=time.time,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    target_sessions = int(target_sessions)
    if target_sessions <= 0:
        raise ValueError("target_sessions must be > 0")
    schedule_id = str(schedule_id or f"pilot_schedule_{int(now_fn())}")
    state_path = Path(schedule_state_json_path) if str(schedule_state_json_path or "").strip() else None
    state: dict[str, Any] = {"schedule_id": schedule_id, "target_sessions": target_sessions, "sessions": [], "stop_reason": ""}
    if state_path and state_path.exists():
        if not bool(resume_schedule):
            raise ValueError("schedule state file already exists; pass --resume-schedule to continue")
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("invalid schedule state file")
        loaded_id = str(loaded.get("schedule_id") or "")
        if loaded_id and loaded_id != schedule_id:
            raise ValueError("schedule_id does not match existing schedule state")
        schedule_state_validation = validate_live_pilot_schedule_state(loaded, strict=bool(resume_state_strict))
        if not bool(schedule_state_validation.get("ok", False)):
            raise ValueError(f"invalid schedule state on resume: {','.join(list(schedule_state_validation.get('errors', []) or []))}")
        state = loaded
        state["schedule_id"] = loaded_id or schedule_id
        state["target_sessions"] = target_sessions
        state["sessions"] = list(state.get("sessions") or [])
    started_unix = float(now_fn())
    completed_sessions = list(state.get("sessions") or [])
    stop_reason = str(state.get("stop_reason") or "")
    for session_index in range(len(completed_sessions), target_sessions):
        if stop_reason:
            break
        if float(schedule_max_duration_seconds or 0.0) > 0 and (float(now_fn()) - started_unix) >= float(schedule_max_duration_seconds):
            stop_reason = "schedule_timebox_elapsed"
            break
        campaign = run_campaign_fn(session_index)
        campaign = dict(campaign or {})
        csum = dict(campaign.get("campaign_summary") or {})
        completed_sessions.append(
            {
                "session_index": session_index,
                "campaign_id": str(csum.get("campaign_id") or ""),
                "campaign_summary": csum,
                "report_path": str(campaign.get("report_path") or ""),
                "state_path": str(campaign.get("state_path") or ""),
            }
        )
        c_stop = str(csum.get("stop_reason") or "")
        if bool(stop_on_campaign_stop_reason) and c_stop:
            stop_reason = f"campaign_stop:{c_stop}"
        state = {
            "schedule_id": schedule_id,
            "target_sessions": target_sessions,
            "sessions": completed_sessions,
            "stop_reason": stop_reason,
        }
        if state_path:
            state_path.write_text(json.dumps(state, sort_keys=True, indent=2), encoding="utf-8")
        if session_index + 1 < target_sessions and not stop_reason and float(session_interval_seconds or 0.0) > 0:
            sleep_fn(float(session_interval_seconds))
    campaign_reports = [{"campaign_summary": dict(s.get("campaign_summary") or {})} for s in completed_sessions]
    daily_report = build_live_pilot_daily_operator_report(
        campaign_reports,
        date_label=str(daily_operator_date_label or ""),
        recommendation_config=recommendation_config,
    )
    if str(daily_operator_report_path or "").strip():
        write_live_pilot_daily_operator_report(daily_report, str(daily_operator_report_path))
    schedule_summary = {
        "schedule_id": schedule_id,
        "target_sessions": target_sessions,
        "completed_sessions": len(completed_sessions),
        "stop_reason": stop_reason or ("" if len(completed_sessions) >= target_sessions else "interrupted"),
        "timebox_elapsed": str(stop_reason) == "schedule_timebox_elapsed",
    }
    report = {
        "schedule_summary": schedule_summary,
        "sessions": completed_sessions,
        "resume_used": bool(resume_schedule),
        "state_path": str(state_path) if state_path else "",
        "daily_operator_report": daily_report,
    }
    if state_path and state_path.exists():
        report["schedule_state_validation"] = validate_live_pilot_schedule_state(state, strict=False)
    if str(schedule_report_path or "").strip():
        _write_campaign_schedule_report(report, str(schedule_report_path))
        report["report_path"] = str(schedule_report_path)
    return report


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
    initial_alerts: list[dict[str, Any]] | None = None,
    campaign_extra_summary: dict[str, Any] | None = None,
    resume_state_strict: bool = False,
) -> dict[str, Any]:
    campaign_runs = int(campaign_runs)
    if campaign_runs <= 0:
        raise ValueError("campaign_runs must be > 0")
    stop_evaluator = stop_evaluator or _default_campaign_stop_evaluator
    campaign_id = str(campaign_id or f"pilot_campaign_{int(time.time())}")
    alert_policy = dict(alert_policy or {})
    alerts_emitted: list[dict[str, Any]] = [dict(x) for x in list(initial_alerts or []) if isinstance(x, dict)]

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
        campaign_state_validation = validate_live_pilot_campaign_state(loaded, strict=bool(resume_state_strict))
        if not bool(campaign_state_validation.get("ok", False)):
            raise ValueError(f"invalid campaign state on resume: {','.join(list(campaign_state_validation.get('errors', []) or []))}")
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
    new_runs_executed = 0
    for run_index in range(started_from_count, campaign_runs):
        if stop_reason:
            break
        out = run_once_fn()
        new_runs_executed += 1
        run_rollup = dict((out or {}).get("rollup") or {})
        run_summary = dict((out or {}).get("live_pilot_summary") or {})
        run_gate = dict((out or {}).get("promotion_gate_summary") or {})
        campaign_provider = dict((out or {}).get("campaign_provider") or {})
        sig = str(run_summary.get("submitted_signature") or "")
        completed_runs.append(
            {
                "run_index": run_index,
                "audit_log_path": str((out or {}).get("audit_log_path") or ""),
                "rollup": run_rollup,
                "live_pilot_summary": run_summary,
                "promotion_gate_summary": run_gate,
                "campaign_provider": campaign_provider,
                "submitted_signature": sig,
            }
        )
        _accumulate_campaign_rollup(aggregate_rollup, run_rollup)
        if bool(campaign_provider.get("failover_applied", False)):
            row = {
                "run_index": run_index,
                "alert_type": "discovery_provider_failover",
                "level": "warning",
                "message": f"Discovery provider failover applied: {campaign_provider.get('from_provider')} -> {campaign_provider.get('next_provider') or campaign_provider.get('provider')}",
                "details": {
                    "from_provider": campaign_provider.get("from_provider"),
                    "to_provider": campaign_provider.get("next_provider") or campaign_provider.get("provider"),
                    "reason": campaign_provider.get("failover_reason"),
                },
            }
            alerts_emitted.append(row)
            if alert_emitter is not None:
                alert_emitter(row)
        if bool(campaign_provider.get("execution_error", False)):
            row = {
                "run_index": run_index,
                "alert_type": "discovery_provider_execution_error",
                "level": "critical",
                "message": f"Discovery provider execution error ({campaign_provider.get('provider')}): {campaign_provider.get('error')}",
                "details": {"provider": campaign_provider.get("provider"), "error": campaign_provider.get("error")},
            }
            alerts_emitted.append(row)
            if alert_emitter is not None:
                alert_emitter(row)
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
    gate_rollup = _build_campaign_adaptive_gate_rollup(aggregate_clean, campaign_extra_summary)
    campaign_gate = _evaluate_live_pilot_promotion_gates(gate_rollup, promotion_gate_config)
    campaign_summary = {
        "campaign_id": state.get("campaign_id") or campaign_id,
        "target_runs": campaign_runs,
        "completed_runs": len(completed_runs),
        "stop_reason": stop_reason or ("" if len(completed_runs) >= campaign_runs else "interrupted"),
        "aggregate_rollup": aggregate_clean,
        "promotion_gate_summary": campaign_gate,
        "alert_summary": _summarize_campaign_alerts(alerts_emitted),
        "discovery_provider_summary": _summarize_campaign_discovery_providers(completed_runs),
    }
    if isinstance(campaign_extra_summary, dict):
        campaign_summary.update(dict(campaign_extra_summary))
    if bool(alert_on_promotion_gate_fail) and new_runs_executed > 0 and str(campaign_gate.get("status") or "") == "fail":
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
    if state_path and state_path.exists():
        report["campaign_state_validation"] = validate_live_pilot_campaign_state(state, strict=False)
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
    if mode == "pilot_campaign_schedule_tiny_supervised":
        _set_default("schedule_sessions", 3)
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
    p.add_argument("--resume-state-strict", action="store_true")
    p.add_argument("--alerts-jsonl-path", default="")
    p.add_argument("--alert-console", action="store_true")
    p.add_argument("--alert-webhook-url", default="")
    p.add_argument("--alert-on-promotion-gate-fail", action="store_true")
    p.add_argument("--alert-on-campaign-stop", action="store_true")
    p.add_argument("--alert-quiet-hours-start-hour-utc", type=int, default=None)
    p.add_argument("--alert-quiet-hours-end-hour-utc", type=int, default=None)
    p.add_argument("--alert-suppress-noncritical-during-quiet-hours", action="store_true")
    p.add_argument("--alert-escalate-warning-to-critical", action="store_true")
    p.add_argument("--campaign-report-glob", default="")
    p.add_argument("--campaign-trend-report-path", default="")
    p.add_argument("--daily-operator-report-path", default="")
    p.add_argument("--daily-operator-date-label", default="")
    p.add_argument("--artifact-index-path", default="")
    p.add_argument("--handoff-snapshot-path", default="")
    p.add_argument("--handoff-shift-label", default="")
    p.add_argument("--handoff-operator-id", default="")
    p.add_argument("--handoff-notes", default="")
    p.add_argument("--restart-command-hint", default="")
    p.add_argument("--run-manifest-path", default="")
    p.add_argument("--run-manifest-label", default="")
    p.add_argument("--risk-profile-preset", default="")
    p.add_argument("--promotion-step-manifest-path", default="")
    p.add_argument("--promotion-step-name", default="")
    p.add_argument("--prelive-go-no-go-report-path", default="")
    p.add_argument("--prelive-require-operator-ack", action="store_true")
    p.add_argument("--prelive-require-bundle-pass", action="store_true")
    p.add_argument("--promotion-ticket-path", default="")
    p.add_argument("--promotion-ticket-action", default="approve_live_test")
    p.add_argument("--promotion-ticket-expires-seconds", type=float, default=3600.0)
    p.add_argument("--promotion-ticket-consumption-log-jsonl-path", default="")
    p.add_argument("--promotion-ticket-revocation-log-jsonl-path", default="")
    p.add_argument("--promotion-ticket-revocation-audit-report-path", default="")
    p.add_argument("--promotion-ticket-lifecycle-timeline-path", default="")
    p.add_argument("--promotion-ticket-revoke-now", action="store_true")
    p.add_argument("--promotion-ticket-revoke-reason", default="manual_revoke")
    p.add_argument("--promotion-ticket-revoke-only", action="store_true")
    p.add_argument("--launch-intent-manifest-path", default="")
    p.add_argument("--launch-intent-expires-seconds", type=float, default=1800.0)
    p.add_argument("--live-launch-guard-report-path", default="")
    p.add_argument("--ticket-state-consistency-report-path", default="")
    p.add_argument("--launch-authorization-packet-path", default="")
    p.add_argument("--launch-authorization-packet-approval-token-path", default="")
    p.add_argument("--launch-authorization-packet-approval-token-revocation-log-jsonl-path", default="")
    p.add_argument("--launch-authorization-packet-approval-token-audit-report-path", default="")
    p.add_argument("--launch-authorization-packet-approval-token-revoke-now", action="store_true")
    p.add_argument("--launch-authorization-packet-approval-token-revoke-reason", default="manual_revoke")
    p.add_argument("--launch-authorization-packet-approval-token-revoke-only", action="store_true")
    p.add_argument("--launch-authorization-packet-approval-action", default="approve_live_launch_packet")
    p.add_argument("--launch-authorization-packet-approval-expires-seconds", type=float, default=900.0)
    p.add_argument("--launch-authorization-freshness-envelope-path", default="")
    p.add_argument("--launch-authorization-chain-report-path", default="")
    p.add_argument("--live-launch-guard-enforce", action="store_true")
    p.add_argument("--live-launch-guard-require-prelive", action="store_true")
    p.add_argument("--live-launch-guard-require-bundle-pass", action="store_true")
    p.add_argument("--live-launch-guard-require-ticket", action="store_true")
    p.add_argument("--live-launch-guard-require-unused-ticket", action="store_true")
    p.add_argument("--live-launch-guard-require-unrevoked-ticket", action="store_true")
    p.add_argument("--live-launch-guard-revocation-reason-class-policy", action="append", default=[])
    p.add_argument("--live-launch-guard-consume-ticket-on-allow", action="store_true")
    p.add_argument("--live-launch-guard-require-launch-intent", action="store_true")
    p.add_argument("--live-launch-guard-require-authorization-packet", action="store_true")
    p.add_argument("--live-launch-guard-require-authorization-packet-binding", action="store_true")
    p.add_argument("--live-launch-guard-require-authorization-packet-approval-token", action="store_true")
    p.add_argument("--live-launch-guard-require-unrevoked-authorization-packet-approval-token", action="store_true")
    p.add_argument("--live-launch-guard-ticket-action", default="approve_live_test")
    p.add_argument("--live-launch-guard-authorization-packet-approval-action", default="approve_live_launch_packet")
    p.add_argument("--live-launch-guard-max-prelive-age-seconds", type=float, default=3600.0)
    p.add_argument("--live-launch-guard-max-launch-intent-age-seconds", type=float, default=1800.0)
    p.add_argument("--live-launch-guard-max-authorization-packet-age-seconds", type=float, default=900.0)
    p.add_argument("--live-launch-guard-max-authorization-packet-approval-token-age-seconds", type=float, default=900.0)
    p.add_argument("--postrun-review-packet-path", default="")
    p.add_argument("--archive-rotation-glob", default="")
    p.add_argument("--archive-rotation-dir", default="")
    p.add_argument("--archive-rotation-keep", type=int, default=10)
    p.add_argument("--archive-rotation-report-path", default="")
    p.add_argument("--operator-decision-log-jsonl-path", default="")
    p.add_argument("--operator-decision-actor", default="")
    p.add_argument("--operator-decision-action", default="")
    p.add_argument("--operator-decision-notes", default="")
    p.add_argument("--schedule-sessions", type=int, default=0)
    p.add_argument("--schedule-id", default="")
    p.add_argument("--schedule-session-interval-seconds", type=float, default=0.0)
    p.add_argument("--schedule-max-duration-seconds", type=float, default=0.0)
    p.add_argument("--schedule-state-json-path", default="")
    p.add_argument("--schedule-report-path", default="")
    p.add_argument("--resume-schedule", action="store_true")
    p.add_argument("--schedule-stop-on-campaign-stop", action="store_true")
    p.add_argument("--bundle-verification-report-path", default="")
    p.add_argument("--timeline-export-path", default="")
    p.add_argument("--discovery-provider-order", default="")
    p.add_argument("--fallback-candidate-list-json-path", default="")
    p.add_argument("--provider-failover-on-transport-error", action="store_true")
    p.add_argument("--fallback-candidate-probe-count", type=int, default=0)
    p.add_argument("--fallback-candidate-probe-fail-closed", action="store_true")
    p.add_argument("--fallback-candidate-probe-min-pass-rate", type=float, default=0.5)
    p.add_argument("--fallback-candidate-probe-warn-failure-rate", type=float, default=0.5)
    p.add_argument("--adaptive-reliability-state-json-path", default="")
    p.add_argument("--adaptive-fallback-candidate-ordering", action="store_true")
    p.add_argument("--adaptive-provider-ordering", action="store_true")
    p.add_argument("--adaptive-candidate-quarantine-threshold", type=int, default=3)
    p.add_argument("--adaptive-candidate-quarantine-ttl-hours", type=float, default=72.0)
    p.add_argument("--adaptive-reliability-candidate-decay-factor", type=float, default=0.9)
    p.add_argument("--adaptive-reliability-provider-decay-factor", type=float, default=0.95)
    p.add_argument("--alert-on-fallback-pool-quality-degraded", action="store_true")
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
    if str(args.run_manifest_path or "").strip():
        write_live_pilot_run_manifest(
            build_live_pilot_run_manifest(
                args_namespace=args,
                argv=sys.argv[1:],
                phase=(str(args.run_manifest_label or "") or "pre_run"),
            ),
            str(args.run_manifest_path),
        )

    if not args.allow_unsafe_paths:
        ensure_dir_within_base(args.audit_log_dir)
        if args.campaign_state_json_path:
            ensure_dir_within_base(str(Path(args.campaign_state_json_path).parent))
        if args.campaign_report_path:
            ensure_dir_within_base(str(Path(args.campaign_report_path).parent))
        if args.alerts_jsonl_path:
            ensure_dir_within_base(str(Path(args.alerts_jsonl_path).parent))
        if args.campaign_trend_report_path:
            ensure_dir_within_base(str(Path(args.campaign_trend_report_path).parent))
        if args.daily_operator_report_path:
            ensure_dir_within_base(str(Path(args.daily_operator_report_path).parent))
        if args.artifact_index_path:
            ensure_dir_within_base(str(Path(args.artifact_index_path).parent))
        if args.handoff_snapshot_path:
            ensure_dir_within_base(str(Path(args.handoff_snapshot_path).parent))
        if args.run_manifest_path:
            ensure_dir_within_base(str(Path(args.run_manifest_path).parent))
        if args.promotion_step_manifest_path:
            ensure_dir_within_base(str(Path(args.promotion_step_manifest_path).parent))
        if args.prelive_go_no_go_report_path:
            ensure_dir_within_base(str(Path(args.prelive_go_no_go_report_path).parent))
        if args.promotion_ticket_path:
            ensure_dir_within_base(str(Path(args.promotion_ticket_path).parent))
        if args.promotion_ticket_consumption_log_jsonl_path:
            ensure_dir_within_base(str(Path(args.promotion_ticket_consumption_log_jsonl_path).parent))
        if args.promotion_ticket_revocation_log_jsonl_path:
            ensure_dir_within_base(str(Path(args.promotion_ticket_revocation_log_jsonl_path).parent))
        if args.promotion_ticket_revocation_audit_report_path:
            ensure_dir_within_base(str(Path(args.promotion_ticket_revocation_audit_report_path).parent))
        if args.promotion_ticket_lifecycle_timeline_path:
            ensure_dir_within_base(str(Path(args.promotion_ticket_lifecycle_timeline_path).parent))
        if args.launch_intent_manifest_path:
            ensure_dir_within_base(str(Path(args.launch_intent_manifest_path).parent))
        if args.live_launch_guard_report_path:
            ensure_dir_within_base(str(Path(args.live_launch_guard_report_path).parent))
        if args.ticket_state_consistency_report_path:
            ensure_dir_within_base(str(Path(args.ticket_state_consistency_report_path).parent))
        if args.launch_authorization_packet_path:
            ensure_dir_within_base(str(Path(args.launch_authorization_packet_path).parent))
        if args.launch_authorization_packet_approval_token_path:
            ensure_dir_within_base(str(Path(args.launch_authorization_packet_approval_token_path).parent))
        if args.launch_authorization_packet_approval_token_revocation_log_jsonl_path:
            ensure_dir_within_base(str(Path(args.launch_authorization_packet_approval_token_revocation_log_jsonl_path).parent))
        if args.launch_authorization_packet_approval_token_audit_report_path:
            ensure_dir_within_base(str(Path(args.launch_authorization_packet_approval_token_audit_report_path).parent))
        if args.launch_authorization_freshness_envelope_path:
            ensure_dir_within_base(str(Path(args.launch_authorization_freshness_envelope_path).parent))
        if args.launch_authorization_chain_report_path:
            ensure_dir_within_base(str(Path(args.launch_authorization_chain_report_path).parent))
        if args.postrun_review_packet_path:
            ensure_dir_within_base(str(Path(args.postrun_review_packet_path).parent))
        if args.archive_rotation_dir:
            ensure_dir_within_base(str(args.archive_rotation_dir))
        if args.archive_rotation_report_path:
            ensure_dir_within_base(str(Path(args.archive_rotation_report_path).parent))
        if args.operator_decision_log_jsonl_path:
            ensure_dir_within_base(str(Path(args.operator_decision_log_jsonl_path).parent))
        if args.bundle_verification_report_path:
            ensure_dir_within_base(str(Path(args.bundle_verification_report_path).parent))
        if args.timeline_export_path:
            ensure_dir_within_base(str(Path(args.timeline_export_path).parent))
        if args.schedule_state_json_path:
            ensure_dir_within_base(str(Path(args.schedule_state_json_path).parent))
        if args.schedule_report_path:
            ensure_dir_within_base(str(Path(args.schedule_report_path).parent))
        if args.adaptive_reliability_state_json_path:
            ensure_dir_within_base(str(Path(args.adaptive_reliability_state_json_path).parent))

    adapter_config = None
    if args.adapter_config_json and args.adapter_config_json_path:
        raise ValueError("provide only one of --adapter-config-json or --adapter-config-json-path")
    if args.adapter_config_json_path:
        adapter_config = json.loads(Path(args.adapter_config_json_path).read_text(encoding="utf-8"))
    elif args.adapter_config_json:
        adapter_config = json.loads(args.adapter_config_json)
    if bool(args.promotion_ticket_revoke_now):
        ticket_for_revoke = _read_json_or_empty(str(args.promotion_ticket_path or ""))
        revoked_row = revoke_live_pilot_promotion_ticket(
            revocation_log_jsonl_path=str(args.promotion_ticket_revocation_log_jsonl_path or ""),
            ticket=ticket_for_revoke,
            operator_id=str(args.operator_decision_actor or args.handoff_operator_id or ""),
            reason=str(args.promotion_ticket_revoke_reason or "manual_revoke"),
        )
        print(json.dumps({"promotion_ticket_revoked": revoked_row}, sort_keys=True))
        if bool(args.promotion_ticket_revoke_only):
            return 0
    if bool(args.launch_authorization_packet_approval_token_revoke_now):
        token_for_revoke = _read_json_or_empty(str(args.launch_authorization_packet_approval_token_path or ""))
        revoked_token_row = revoke_live_pilot_launch_authorization_packet_approval_token(
            revocation_log_jsonl_path=str(args.launch_authorization_packet_approval_token_revocation_log_jsonl_path or ""),
            approval_token=token_for_revoke,
            operator_id=str(args.operator_decision_actor or args.handoff_operator_id or ""),
            reason=str(args.launch_authorization_packet_approval_token_revoke_reason or "manual_revoke"),
        )
        print(json.dumps({"launch_authorization_packet_approval_token_revoked": revoked_token_row}, sort_keys=True))
        if bool(args.launch_authorization_packet_approval_token_revoke_only):
            return 0
    if bool(args.live_launch_guard_enforce):
        prelive_guard_obj = _read_json_or_empty(str(args.prelive_go_no_go_report_path or ""))
        ticket_guard_obj = _read_json_or_empty(str(args.promotion_ticket_path or ""))
        launch_intent_guard_obj = _read_json_or_empty(str(args.launch_intent_manifest_path or ""))
        launch_authorization_packet_obj = _read_json_or_empty(str(args.launch_authorization_packet_path or ""))
        launch_authorization_packet_approval_token_obj = _read_json_or_empty(str(args.launch_authorization_packet_approval_token_path or ""))
        revocation_policy_overrides = _parse_simple_policy_overrides(list(args.live_launch_guard_revocation_reason_class_policy or []))
        consumed_ticket_rows = list_live_pilot_promotion_ticket_consumptions(str(args.promotion_ticket_consumption_log_jsonl_path or ""))
        revoked_ticket_rows = list_live_pilot_promotion_ticket_revocations(str(args.promotion_ticket_revocation_log_jsonl_path or ""))
        revoked_auth_token_rows = list_live_pilot_launch_authorization_packet_approval_token_revocations(
            str(args.launch_authorization_packet_approval_token_revocation_log_jsonl_path or "")
        )
        live_guard = evaluate_live_launch_guard(
            adapter_config=(adapter_config if isinstance(adapter_config, dict) else None),
            enable_live_auto_submit_window=bool(args.enable_live_auto_submit_window),
            prelive_go_no_go_report=prelive_guard_obj,
            promotion_ticket=ticket_guard_obj,
            launch_intent_manifest=launch_intent_guard_obj,
            launch_authorization_packet=launch_authorization_packet_obj,
            launch_authorization_packet_approval_token=launch_authorization_packet_approval_token_obj,
            requested_mode=str(args.mode or ""),
            requested_risk_profile_preset=str(args.risk_profile_preset or ""),
            require_prelive_go_no_go=bool(args.live_launch_guard_require_prelive),
            require_bundle_pass=bool(args.live_launch_guard_require_bundle_pass),
            require_operator_ticket=bool(args.live_launch_guard_require_ticket),
            require_unused_ticket=bool(args.live_launch_guard_require_unused_ticket),
            require_unrevoked_ticket=bool(args.live_launch_guard_require_unrevoked_ticket),
            require_launch_intent=bool(args.live_launch_guard_require_launch_intent),
            require_launch_authorization_packet=bool(args.live_launch_guard_require_authorization_packet),
            require_launch_authorization_packet_binding=bool(args.live_launch_guard_require_authorization_packet_binding),
            require_launch_authorization_packet_approval_token=bool(args.live_launch_guard_require_authorization_packet_approval_token),
            require_unrevoked_launch_authorization_packet_approval_token=bool(
                args.live_launch_guard_require_unrevoked_authorization_packet_approval_token
            ),
            revocation_reason_class_policy_overrides=revocation_policy_overrides,
            required_ticket_action=str(args.live_launch_guard_ticket_action or "approve_live_test"),
            required_launch_authorization_packet_approval_action=str(args.live_launch_guard_authorization_packet_approval_action or "approve_live_launch_packet"),
            max_prelive_age_seconds=float(args.live_launch_guard_max_prelive_age_seconds or 3600.0),
            max_launch_intent_age_seconds=float(args.live_launch_guard_max_launch_intent_age_seconds or 1800.0),
            max_launch_authorization_packet_age_seconds=float(args.live_launch_guard_max_authorization_packet_age_seconds or 900.0),
            max_launch_authorization_packet_approval_token_age_seconds=float(args.live_launch_guard_max_authorization_packet_approval_token_age_seconds or 900.0),
            consumed_tickets=consumed_ticket_rows,
            revoked_tickets=revoked_ticket_rows,
            revoked_launch_authorization_packet_approval_tokens=revoked_auth_token_rows,
        )
        if str(args.promotion_ticket_revocation_audit_report_path or "").strip():
            revocation_audit_report = build_live_pilot_promotion_ticket_revocation_audit_summary(
                ticket=ticket_guard_obj,
                consumed_tickets=consumed_ticket_rows,
                revoked_tickets=revoked_ticket_rows,
                revocation_reason_class_policy_overrides=revocation_policy_overrides,
            )
            write_live_pilot_promotion_ticket_revocation_audit_summary(
                revocation_audit_report,
                str(args.promotion_ticket_revocation_audit_report_path),
            )
        else:
            revocation_audit_report = {}
        if str(args.promotion_ticket_lifecycle_timeline_path or "").strip():
            ticket_lifecycle_timeline = build_live_pilot_promotion_ticket_lifecycle_timeline(
                promotion_ticket=ticket_guard_obj,
                consumed_tickets=consumed_ticket_rows,
                revoked_tickets=revoked_ticket_rows,
                launch_guard_report=live_guard,
                revocation_reason_class_policy_overrides=revocation_policy_overrides,
            )
            write_live_pilot_promotion_ticket_lifecycle_timeline(ticket_lifecycle_timeline, str(args.promotion_ticket_lifecycle_timeline_path))
        else:
            ticket_lifecycle_timeline = {}
        if str(args.ticket_state_consistency_report_path or "").strip():
            ticket_state_consistency_report = build_live_pilot_ticket_state_consistency_report(
                promotion_ticket=ticket_guard_obj,
                launch_intent_manifest=launch_intent_guard_obj,
                prelive_go_no_go_report=prelive_guard_obj,
                consumed_tickets=consumed_ticket_rows,
                revoked_tickets=revoked_ticket_rows,
                revocation_reason_class_policy_overrides=revocation_policy_overrides,
                launch_guard_report=live_guard,
                max_prelive_age_seconds=float(args.live_launch_guard_max_prelive_age_seconds or 3600.0),
                max_launch_intent_age_seconds=float(args.live_launch_guard_max_launch_intent_age_seconds or 1800.0),
            )
            write_live_pilot_ticket_state_consistency_report(
                ticket_state_consistency_report,
                str(args.ticket_state_consistency_report_path),
            )
        else:
            ticket_state_consistency_report = {}
        if str(args.launch_authorization_packet_path or "").strip():
            launch_authorization_packet_out = build_live_pilot_launch_authorization_packet(
                prelive_go_no_go_report=prelive_guard_obj,
                promotion_ticket=ticket_guard_obj,
                launch_intent_manifest=launch_intent_guard_obj,
                live_launch_guard_report=live_guard,
                ticket_state_consistency_report=ticket_state_consistency_report,
                revocation_audit_report=revocation_audit_report,
                ticket_lifecycle_timeline=ticket_lifecycle_timeline,
            )
            write_live_pilot_launch_authorization_packet(launch_authorization_packet_out, str(args.launch_authorization_packet_path))
        else:
            launch_authorization_packet_out = {}
        if str(args.launch_authorization_packet_approval_token_path or "").strip():
            write_live_pilot_launch_authorization_packet_approval_token(
                build_live_pilot_launch_authorization_packet_approval_token(
                    launch_authorization_packet=(launch_authorization_packet_out or launch_authorization_packet_obj),
                    operator_id=str(args.operator_decision_actor or args.handoff_operator_id or "main_user"),
                    approval_action=str(args.launch_authorization_packet_approval_action or "approve_live_launch_packet"),
                    expires_in_seconds=float(args.launch_authorization_packet_approval_expires_seconds or 900.0),
                ),
                str(args.launch_authorization_packet_approval_token_path),
            )
        packet_approval_token_obj_effective = _read_json_or_empty(str(args.launch_authorization_packet_approval_token_path or "")) or launch_authorization_packet_approval_token_obj
        if str(args.launch_authorization_packet_approval_token_audit_report_path or "").strip():
            packet_approval_token_audit_report = build_live_pilot_launch_authorization_packet_approval_token_audit_summary(
                approval_token=packet_approval_token_obj_effective,
                revoked_approval_tokens=revoked_auth_token_rows,
            )
            write_live_pilot_launch_authorization_packet_approval_token_audit_summary(
                packet_approval_token_audit_report,
                str(args.launch_authorization_packet_approval_token_audit_report_path),
            )
        else:
            packet_approval_token_audit_report = {}
        if str(args.launch_authorization_freshness_envelope_path or "").strip():
            freshness_envelope_report = build_live_pilot_launch_authorization_freshness_envelope(
                launch_authorization_packet=(launch_authorization_packet_out or launch_authorization_packet_obj),
                max_packet_age_seconds=float(args.live_launch_guard_max_authorization_packet_age_seconds or 900.0),
            )
            write_live_pilot_launch_authorization_freshness_envelope(
                freshness_envelope_report,
                str(args.launch_authorization_freshness_envelope_path),
            )
        else:
            freshness_envelope_report = {}
        if str(args.launch_authorization_chain_report_path or "").strip():
            write_live_pilot_launch_authorization_chain_report(
                build_live_pilot_launch_authorization_chain_report(
                    launch_authorization_packet=(launch_authorization_packet_out or launch_authorization_packet_obj),
                    launch_authorization_packet_approval_token=packet_approval_token_obj_effective,
                    launch_authorization_packet_approval_token_audit_summary=packet_approval_token_audit_report,
                    promotion_ticket_revocation_audit_summary=revocation_audit_report,
                    live_launch_guard_report=live_guard,
                    launch_authorization_freshness_envelope=freshness_envelope_report,
                ),
                str(args.launch_authorization_chain_report_path),
            )
        if str(args.live_launch_guard_report_path or "").strip():
            write_live_launch_guard_report(live_guard, str(args.live_launch_guard_report_path))
        if str(live_guard.get("status") or "") != "allow":
            print(json.dumps({"live_launch_guard": live_guard}, sort_keys=True))
            return 3
        if (
            bool(args.live_launch_guard_consume_ticket_on_allow)
            and bool(args.live_launch_guard_require_ticket)
            and bool(args.live_launch_guard_require_unused_ticket)
        ):
            consume_live_pilot_promotion_ticket(
                consumption_log_jsonl_path=str(args.promotion_ticket_consumption_log_jsonl_path or ""),
                ticket=ticket_guard_obj,
                reason="live_launch_guard_allow",
            )
    if str(args.campaign_report_glob or "").strip():
        report_paths = sorted(Path(".").glob(str(args.campaign_report_glob)))
        reports = []
        for pth in report_paths:
            try:
                data = json.loads(pth.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                reports.append(data)
        trend_report = aggregate_live_pilot_campaign_reports(
            reports,
            recommendation_config=((adapter_config or {}).get("live_pilot_multi_campaign_recommendation") if isinstance(adapter_config, dict) else None),
        )
        trend_report["report_paths"] = [str(p) for p in report_paths]
        if str(args.campaign_trend_report_path or "").strip():
            write_campaign_trend_report(trend_report, str(args.campaign_trend_report_path))
        if str(args.daily_operator_report_path or "").strip():
            daily_report = build_live_pilot_daily_operator_report(
                reports,
                date_label=str(args.daily_operator_date_label or ""),
                recommendation_config=((adapter_config or {}).get("live_pilot_multi_campaign_recommendation") if isinstance(adapter_config, dict) else None),
            )
            if str(args.operator_decision_action or "").strip():
                decision_row = append_live_pilot_operator_decision_log(
                    path_str=str(args.operator_decision_log_jsonl_path or ""),
                    daily_report=daily_report,
                    operator_id=str(args.operator_decision_actor or ""),
                    action=str(args.operator_decision_action or ""),
                    notes=str(args.operator_decision_notes or ""),
                )
                daily_report = apply_operator_acknowledgement_to_daily_report(daily_report, decision_row)
            write_live_pilot_daily_operator_report(daily_report, str(args.daily_operator_report_path))
            if str(args.artifact_index_path or "").strip():
                handoff = {}
                artifact_index = build_live_pilot_artifact_index(
                    date_label=str(args.daily_operator_date_label or ""),
                    daily_operator_report=daily_report,
                    daily_operator_report_path=str(args.daily_operator_report_path or ""),
                    alerts_jsonl_path=str(args.alerts_jsonl_path or ""),
                    operator_decision_log_jsonl_path=str(args.operator_decision_log_jsonl_path or ""),
                    campaign_reports=reports,
                    campaign_report_paths=[str(p) for p in report_paths],
                )
                artifact_index.setdefault("artifacts", {})["artifact_index"] = {"path": str(args.artifact_index_path or ""), "present": True}
                write_live_pilot_artifact_index(artifact_index, str(args.artifact_index_path))
                if str(args.handoff_snapshot_path or "").strip():
                    handoff = build_live_pilot_handoff_snapshot(
                        daily_operator_report=daily_report,
                        artifact_index=artifact_index,
                        handoff_operator_id=str(args.handoff_operator_id or args.operator_decision_actor or ""),
                        shift_label=str(args.handoff_shift_label or ""),
                        handoff_notes=str(args.handoff_notes or ""),
                        restart_command_hint=str(args.restart_command_hint or ""),
                    )
                    write_live_pilot_handoff_snapshot(handoff, str(args.handoff_snapshot_path))
                bundle_verification = None
                if str(args.bundle_verification_report_path or "").strip():
                    bundle_verification = verify_live_pilot_validation_bundle(artifact_index=artifact_index)
                    write_live_pilot_bundle_verification(bundle_verification, str(args.bundle_verification_report_path))
                if str(args.promotion_step_manifest_path or "").strip() and str(args.risk_profile_preset or "").strip():
                    write_live_pilot_promotion_step_manifest(
                        build_live_pilot_promotion_step_manifest(
                            risk_profile_preset=str(args.risk_profile_preset),
                            step_name=str(args.promotion_step_name or ""),
                            daily_operator_report=daily_report,
                            artifact_index=artifact_index,
                            bundle_verification=(bundle_verification or verify_live_pilot_validation_bundle(artifact_index=artifact_index)),
                            operator_decision_log_path=str(args.operator_decision_log_jsonl_path or ""),
                        ),
                        str(args.promotion_step_manifest_path),
                    )
                if str(args.prelive_go_no_go_report_path or "").strip():
                    prelive_obj = build_live_pilot_prelive_go_no_go_checklist(
                        daily_operator_report=daily_report,
                        bundle_verification=(bundle_verification or verify_live_pilot_validation_bundle(artifact_index=artifact_index)),
                        handoff_snapshot=(handoff if 'handoff' in locals() else {}),
                        risk_profile_preset=str(args.risk_profile_preset or ""),
                        required_operator_ack=bool(args.prelive_require_operator_ack),
                        require_bundle_pass=bool(args.prelive_require_bundle_pass),
                    )
                    write_live_pilot_prelive_go_no_go_checklist(
                        prelive_obj,
                        str(args.prelive_go_no_go_report_path),
                    )
                else:
                    prelive_obj = {}
                if str(args.launch_intent_manifest_path or "").strip():
                    launch_intent_obj = build_live_pilot_launch_intent_manifest(
                        mode=str(args.mode or ""),
                        risk_profile_preset=str(args.risk_profile_preset or ""),
                        enable_live_auto_submit_window=bool(args.enable_live_auto_submit_window),
                        adapter_config=(adapter_config if isinstance(adapter_config, dict) else None),
                        prelive_go_no_go_report=prelive_obj,
                        expires_in_seconds=float(args.launch_intent_expires_seconds or 1800.0),
                    )
                    write_live_pilot_launch_intent_manifest(launch_intent_obj, str(args.launch_intent_manifest_path))
                else:
                    launch_intent_obj = {}
                if str(args.promotion_ticket_path or "").strip():
                    write_live_pilot_promotion_ticket(
                        build_live_pilot_promotion_ticket(
                            operator_id=str(args.operator_decision_actor or args.handoff_operator_id or ""),
                            approval_action=str(args.promotion_ticket_action or "approve_live_test"),
                            risk_profile_preset=str(args.risk_profile_preset or ""),
                            promotion_step_manifest=_read_json_or_empty(str(args.promotion_step_manifest_path or "")),
                            prelive_go_no_go_report=prelive_obj,
                            launch_intent_manifest=launch_intent_obj,
                            expires_in_seconds=float(args.promotion_ticket_expires_seconds or 3600.0),
                        ),
                        str(args.promotion_ticket_path),
                    )
        print(json.dumps(trend_report, sort_keys=True))
        return 0
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
    fallback_candidate_list = None
    adaptive_reliability_state = load_adaptive_reliability_state(str(args.adaptive_reliability_state_json_path or ""))
    ars_meta = dict(adaptive_reliability_state.get("meta") or {})
    ars_meta["candidate_decay_factor"] = float(args.adaptive_reliability_candidate_decay_factor or 0.9)
    ars_meta["provider_decay_factor"] = float(args.adaptive_reliability_provider_decay_factor or 0.95)
    adaptive_reliability_state["meta"] = ars_meta
    adaptive_fallback_summary: dict[str, Any] = {}
    adaptive_provider_order_summary: dict[str, Any] = {}
    if args.fallback_candidate_list_json_path:
        fallback_candidate_list = json.loads(Path(args.fallback_candidate_list_json_path).read_text(encoding="utf-8"))
    fallback_candidate_preflight_summary: dict[str, Any] = {}
    campaign_initial_alerts: list[dict[str, Any]] = []
    if isinstance(fallback_candidate_list, list):
        sanitized = sanitize_fallback_candidates(fallback_candidate_list)
        fallback_candidate_list = list(sanitized.get("candidates") or [])
        fallback_candidate_preflight_summary.update(dict(sanitized.get("summary") or {}))
        reordered = adaptive_reorder_fallback_candidates(
            fallback_candidate_list,
            reliability_state=adaptive_reliability_state,
            enabled=bool(args.adaptive_fallback_candidate_ordering),
            quarantine_failure_threshold=int(args.adaptive_candidate_quarantine_threshold or 3),
            quarantine_ttl_seconds=float(args.adaptive_candidate_quarantine_ttl_hours or 0.0) * 3600.0,
        )
        fallback_candidate_list = list(reordered.get("candidates") or [])
        adaptive_fallback_summary.update(dict(reordered.get("summary") or {}))
        probe_count = int(args.fallback_candidate_probe_count or 0)
        if probe_count > 0:
            probed = probe_fallback_candidates_preflight(
                fallback_candidate_list,
                probe_count=probe_count,
                adapter_config=adapter_config,
                quote_probe_fail_closed=bool(args.fallback_candidate_probe_fail_closed),
                quote_probe_min_pass_rate=float(args.fallback_candidate_probe_min_pass_rate or 0.5),
                quote_probe_warn_failure_rate=float(args.fallback_candidate_probe_warn_failure_rate or 0.5),
            )
            fallback_candidate_list = list(probed.get("candidates") or [])
            fallback_candidate_preflight_summary.update(dict(probed.get("summary") or {}))
            pass_rate = _to_float_or_none(fallback_candidate_preflight_summary.get("fallback_candidates_probe_pass_rate"))
            if pass_rate is not None:
                fail_rate = max(0.0, 1.0 - float(pass_rate))
                if fail_rate >= float(args.fallback_candidate_probe_warn_failure_rate or 0.5):
                    campaign_initial_alerts.append(
                        {
                            "alert_type": "fallback_candidate_probe_failure_rate_high",
                            "level": "warning",
                            "message": f"Fallback candidate probe failure rate is high ({round(fail_rate, 4)}).",
                            "details": {
                                "probe_pass_rate": pass_rate,
                                "probe_failed": fallback_candidate_preflight_summary.get("fallback_candidates_probe_failed"),
                                "probe_count": fallback_candidate_preflight_summary.get("fallback_candidates_probe_count"),
                            },
                        }
                    )
            if bool(fallback_candidate_preflight_summary.get("fallback_candidates_probe_fail_closed_triggered", False)):
                campaign_initial_alerts.append(
                    {
                        "alert_type": "fallback_candidate_probe_fail_closed_abort",
                        "level": "critical",
                        "message": "Fallback candidate probe fail-closed threshold triggered.",
                        "details": {
                            "probe_pass_rate": fallback_candidate_preflight_summary.get("fallback_candidates_probe_pass_rate"),
                            "min_pass_rate": fallback_candidate_preflight_summary.get("fallback_candidates_probe_min_pass_rate"),
                        },
                    }
                )
        if (
            bool(args.alert_on_fallback_pool_quality_degraded)
            and int(fallback_candidate_preflight_summary.get("fallback_candidates_total", 0) or 0) > 0
            and int(fallback_candidate_preflight_summary.get("fallback_candidates_probe_ok", 0) or 0)
            < int(fallback_candidate_preflight_summary.get("fallback_candidates_probe_failed", 0) or 0)
        ):
            campaign_initial_alerts.append(
                {
                    "alert_type": "fallback_pool_quality_degraded",
                    "level": "warning",
                    "message": "Fallback candidate pool quality degraded (probe failures exceed probe successes).",
                    "details": {
                        "probe_ok": fallback_candidate_preflight_summary.get("fallback_candidates_probe_ok"),
                        "probe_failed": fallback_candidate_preflight_summary.get("fallback_candidates_probe_failed"),
                    },
                }
            )
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
        provider_order = [s.strip().lower() for s in str(args.discovery_provider_order or "").split(",") if s.strip()]
        if not provider_order:
            provider_order = ["dexscreener" if bool(args.use_dexscreener_signals) else ("candidate_file" if isinstance(candidate_list, list) else "direct")]
        provider_reordered = adaptive_reorder_provider_order(
            provider_order,
            reliability_state=adaptive_reliability_state,
            enabled=bool(args.adaptive_provider_ordering),
        )
        provider_order = list(provider_reordered.get("provider_order") or provider_order)
        adaptive_provider_order_summary = dict(provider_reordered.get("summary") or {})
        provider_state = {"index": 0, "current": (provider_order[0] if provider_order else "direct")}

        def _run_campaign_with_provider_failover():
            provider = str(provider_state.get("current") or "direct")
            out = None
            cp_meta = {"provider": provider, "executed_provider": provider}
            try:
                if provider == "dexscreener":
                    out = _execute_single_run()
                elif provider == "candidate_file":
                    if not isinstance(fallback_candidate_list, list):
                        raise ValueError("candidate_file provider selected but --fallback-candidate-list-json-path not provided")
                    mechanical_safety_filter = _build_live_pilot_mechanical_safety_filter_from_config(adapter_config or {})
                    volatility_guard = _build_live_pilot_volatility_guard_from_config(adapter_config or {})
                    out = run_live_pilot_auto_window_candidates(
                        candidates=fallback_candidate_list,
                        window_seconds=float(args.auto_pilot_window_seconds or 30.0),
                        max_auto_trades=int(args.auto_pilot_max_trades or 1),
                        poll_interval_seconds=float(args.auto_pilot_poll_interval_seconds or 0.0),
                        stop_on_reconciliation_mismatch=bool(args.auto_pilot_stop_on_reconciliation_mismatch),
                        stop_on_reconciliation_inconclusive=bool(args.auto_pilot_stop_on_reconciliation_inconclusive),
                        audit_log_dir=args.audit_log_dir,
                        adapter_config=adapter_config,
                        mechanical_safety_filter=mechanical_safety_filter,
                        volatility_guard=volatility_guard,
                    )
                else:
                    out = _execute_single_run()
            except Exception as exc:
                out = {
                    "audit_log_path": "",
                    "rollup": {"runs": 0, "submit_dispatch_by_reason": {"campaign_provider_execution_error": 1}},
                    "live_pilot_summary": {},
                    "promotion_gate_summary": {},
                }
                cp_meta.update({"execution_error": True, "error": str(exc)})

            rr = dict((out or {}).get("rollup") or {})
            transport_errors = int(((rr.get("signal_provider_metrics") or {}).get("fetch_transport_errors", 0) or 0))
            if (
                bool(args.provider_failover_on_transport_error)
                and provider == "dexscreener"
                and transport_errors > 0
                and provider_state["index"] + 1 < len(provider_order)
            ):
                next_provider = str(provider_order[provider_state["index"] + 1] or "")
                if next_provider:
                    provider_state["index"] += 1
                    provider_state["current"] = next_provider
                    cp_meta.update(
                        {
                            "failover_applied": True,
                            "failover_reason": "signal_provider_transport_error",
                            "from_provider": provider,
                            "next_provider": next_provider,
                        }
                    )
            out["campaign_provider"] = cp_meta
            return out

        def _campaign_stop_evaluator_with_failover(run_out):
            cp = dict((run_out or {}).get("campaign_provider") or {})
            if bool(cp.get("execution_error", False)):
                return {"stop": True, "reason": "campaign_provider_execution_error"}
            d = _default_campaign_stop_evaluator(run_out)
            if (
                str(d.get("reason") or "") == "signal_provider_transport_error"
                and bool(args.provider_failover_on_transport_error)
                and bool(cp.get("failover_applied", False))
            ):
                return {"stop": False, "reason": ""}
            return d

        campaign_alert_emitter = None
        if str(args.alerts_jsonl_path or "").strip() or bool(args.alert_console) or str(args.alert_webhook_url or "").strip():
            escalation_levels = {"warning": "critical"} if bool(args.alert_escalate_warning_to_critical) else {}
            campaign_alert_emitter = _build_campaign_alert_emitter(
                campaign_id=resolved_campaign_id,
                alerts_jsonl_path=str(args.alerts_jsonl_path or ""),
                console=bool(args.alert_console),
                webhook_url=str(args.alert_webhook_url or ""),
                quiet_hours_start_hour_utc=args.alert_quiet_hours_start_hour_utc,
                quiet_hours_end_hour_utc=args.alert_quiet_hours_end_hour_utc,
                allow_critical_during_quiet_hours=bool(args.alert_suppress_noncritical_during_quiet_hours),
                escalation_levels=escalation_levels,
            )
        if bool(fallback_candidate_preflight_summary.get("fallback_candidates_probe_fail_closed_triggered", False)):
            for row in campaign_initial_alerts:
                if campaign_alert_emitter is not None:
                    campaign_alert_emitter(row)
            fail_closed_out = {
                "campaign_summary": {
                    "campaign_id": resolved_campaign_id,
                    "target_runs": int(args.campaign_runs),
                    "completed_runs": 0,
                    "stop_reason": "fallback_candidate_probe_fail_closed_abort",
                    "aggregate_rollup": {},
                    "promotion_gate_summary": _evaluate_live_pilot_promotion_gates({}, ((adapter_config or {}).get("live_pilot_promotion_gates") if isinstance(adapter_config, dict) else None)),
                    "alert_summary": _summarize_campaign_alerts(campaign_initial_alerts),
                    "discovery_provider_summary": _summarize_campaign_discovery_providers([]),
                    "fallback_candidate_probe_summary": dict(fallback_candidate_preflight_summary),
                    "adaptive_fallback_candidate_summary": dict(adaptive_fallback_summary),
                    "adaptive_provider_order_summary": dict(adaptive_provider_order_summary),
                },
                "runs": [],
                "alerts": list(campaign_initial_alerts),
                "resume_used": bool(args.resume_campaign),
                "state_path": str(args.campaign_state_json_path or ""),
            }
            if str(args.campaign_report_path or "").strip():
                _write_campaign_report(fail_closed_out, str(args.campaign_report_path))
                fail_closed_out["report_path"] = str(args.campaign_report_path)
            print(
                json.dumps(
                    {
                        "campaign_summary": fail_closed_out["campaign_summary"],
                        "report_path": fail_closed_out.get("report_path", ""),
                        "state_path": fail_closed_out.get("state_path", ""),
                    },
                    sort_keys=True,
                )
            )
            return 2
        def _run_one_campaign_session(session_index: int = 0, total_sessions: int = 1):
            nonlocal adaptive_reliability_state
            suffix = (f"_s{int(session_index) + 1:03d}" if int(total_sessions) > 1 else "")
            campaign = run_live_pilot_campaign(
                campaign_runs=int(args.campaign_runs),
                run_once_fn=_run_campaign_with_provider_failover,
                campaign_id=(f"{resolved_campaign_id}{suffix}" if suffix else resolved_campaign_id),
                campaign_state_json_path=(_path_with_inserted_suffix(str(args.campaign_state_json_path or ""), suffix) if suffix else str(args.campaign_state_json_path or "")),
                campaign_report_path=(_path_with_inserted_suffix(str(args.campaign_report_path or ""), suffix) if suffix else str(args.campaign_report_path or "")),
                resume_campaign=(bool(args.resume_campaign) if not suffix else False),
                resume_state_strict=bool(args.resume_state_strict),
                promotion_gate_config=((adapter_config or {}).get("live_pilot_promotion_gates") if isinstance(adapter_config, dict) else None),
                stop_evaluator=_campaign_stop_evaluator_with_failover,
                alert_emitter=campaign_alert_emitter,
                alert_policy=((adapter_config or {}).get("live_pilot_campaign_alerts") if isinstance(adapter_config, dict) else None),
                alert_on_promotion_gate_fail=bool(args.alert_on_promotion_gate_fail),
                alert_on_campaign_stop=bool(args.alert_on_campaign_stop),
                initial_alerts=campaign_initial_alerts,
                campaign_extra_summary=(
                    {
                        **({"fallback_candidate_probe_summary": dict(fallback_candidate_preflight_summary)} if fallback_candidate_preflight_summary else {}),
                        **({"adaptive_fallback_candidate_summary": dict(adaptive_fallback_summary)} if adaptive_fallback_summary else {}),
                        **({"adaptive_provider_order_summary": dict(adaptive_provider_order_summary)} if adaptive_provider_order_summary else {}),
                    }
                ),
            )
            if str(args.adaptive_reliability_state_json_path or "").strip():
                adaptive_reliability_state = update_adaptive_reliability_state_from_campaign_report(adaptive_reliability_state, campaign)
                save_adaptive_reliability_state(str(args.adaptive_reliability_state_json_path), adaptive_reliability_state)
            return campaign

        if int(args.schedule_sessions or 0) > 0:
            schedule = run_live_pilot_campaign_schedule(
                target_sessions=int(args.schedule_sessions),
                run_campaign_fn=lambda session_index: _run_one_campaign_session(session_index, int(args.schedule_sessions)),
                schedule_id=str(args.schedule_id or f"{resolved_campaign_id}_schedule"),
                session_interval_seconds=float(args.schedule_session_interval_seconds or 0.0),
                schedule_max_duration_seconds=float(args.schedule_max_duration_seconds or 0.0),
                schedule_state_json_path=str(args.schedule_state_json_path or ""),
                schedule_report_path=str(args.schedule_report_path or ""),
                resume_schedule=bool(args.resume_schedule),
                resume_state_strict=bool(args.resume_state_strict),
                stop_on_campaign_stop_reason=bool(args.schedule_stop_on_campaign_stop),
                daily_operator_report_path=str(args.daily_operator_report_path or ""),
                daily_operator_date_label=str(args.daily_operator_date_label or ""),
                recommendation_config=((adapter_config or {}).get("live_pilot_multi_campaign_recommendation") if isinstance(adapter_config, dict) else None),
            )
            if str(args.daily_operator_report_path or "").strip() and str(args.operator_decision_action or "").strip():
                daily_report = dict(schedule.get("daily_operator_report") or {})
                decision_row = append_live_pilot_operator_decision_log(
                    path_str=str(args.operator_decision_log_jsonl_path or ""),
                    daily_report=daily_report,
                    operator_id=str(args.operator_decision_actor or ""),
                    action=str(args.operator_decision_action or ""),
                    notes=str(args.operator_decision_notes or ""),
                )
                daily_report = apply_operator_acknowledgement_to_daily_report(daily_report, decision_row)
                write_live_pilot_daily_operator_report(daily_report, str(args.daily_operator_report_path))
                schedule["daily_operator_report"] = daily_report
            if str(args.artifact_index_path or "").strip():
                handoff = {}
                sessions = [dict(x) for x in list(schedule.get("sessions") or []) if isinstance(x, dict)]
                campaign_reports_for_index = [{"campaign_summary": dict((s.get("campaign_summary") or {}))} for s in sessions]
                campaign_report_paths = [str(s.get("report_path") or "") for s in sessions if str(s.get("report_path") or "").strip()]
                campaign_state_paths = [str(s.get("state_path") or "") for s in sessions if str(s.get("state_path") or "").strip()]
                artifact_index = build_live_pilot_artifact_index(
                    date_label=str(args.daily_operator_date_label or ""),
                    schedule_report=schedule,
                    schedule_report_path=str(args.schedule_report_path or ""),
                    schedule_state_path=str(args.schedule_state_json_path or ""),
                    daily_operator_report=dict(schedule.get("daily_operator_report") or {}),
                    daily_operator_report_path=str(args.daily_operator_report_path or ""),
                    campaign_reports=campaign_reports_for_index,
                    campaign_report_paths=campaign_report_paths,
                    campaign_state_paths=campaign_state_paths,
                    alerts_jsonl_path=str(args.alerts_jsonl_path or ""),
                    operator_decision_log_jsonl_path=str(args.operator_decision_log_jsonl_path or ""),
                )
                artifact_index.setdefault("artifacts", {})["artifact_index"] = {"path": str(args.artifact_index_path or ""), "present": True}
                write_live_pilot_artifact_index(artifact_index, str(args.artifact_index_path))
                if str(args.handoff_snapshot_path or "").strip():
                    handoff = build_live_pilot_handoff_snapshot(
                        schedule_report=schedule,
                        daily_operator_report=dict(schedule.get("daily_operator_report") or {}),
                        artifact_index=artifact_index,
                        handoff_operator_id=str(args.handoff_operator_id or args.operator_decision_actor or ""),
                        shift_label=str(args.handoff_shift_label or ""),
                        handoff_notes=str(args.handoff_notes or ""),
                        restart_command_hint=str(args.restart_command_hint or ""),
                    )
                    write_live_pilot_handoff_snapshot(handoff, str(args.handoff_snapshot_path))
                if str(args.bundle_verification_report_path or "").strip():
                    bundle_verification = verify_live_pilot_validation_bundle(artifact_index=artifact_index)
                    write_live_pilot_bundle_verification(bundle_verification, str(args.bundle_verification_report_path))
                else:
                    bundle_verification = verify_live_pilot_validation_bundle(artifact_index=artifact_index)
                if str(args.timeline_export_path or "").strip():
                    timeline_obj = build_live_pilot_session_timeline(
                        schedule_report=schedule,
                        alerts_rows=_read_jsonl_rows(str(args.alerts_jsonl_path or "")),
                        operator_decision_rows=_read_jsonl_rows(str(args.operator_decision_log_jsonl_path or "")),
                    )
                    write_live_pilot_session_timeline(
                        timeline_obj,
                        str(args.timeline_export_path),
                    )
                else:
                    timeline_obj = {}
                if str(args.promotion_step_manifest_path or "").strip() and str(args.risk_profile_preset or "").strip():
                    write_live_pilot_promotion_step_manifest(
                        build_live_pilot_promotion_step_manifest(
                            risk_profile_preset=str(args.risk_profile_preset),
                            step_name=str(args.promotion_step_name or ""),
                            daily_operator_report=dict(schedule.get("daily_operator_report") or {}),
                            artifact_index=artifact_index,
                            bundle_verification=bundle_verification,
                            operator_decision_log_path=str(args.operator_decision_log_jsonl_path or ""),
                        ),
                        str(args.promotion_step_manifest_path),
                    )
                if str(args.prelive_go_no_go_report_path or "").strip():
                    prelive_obj = build_live_pilot_prelive_go_no_go_checklist(
                        daily_operator_report=dict(schedule.get("daily_operator_report") or {}),
                        bundle_verification=bundle_verification,
                        handoff_snapshot=handoff,
                        risk_profile_preset=str(args.risk_profile_preset or ""),
                        required_operator_ack=bool(args.prelive_require_operator_ack),
                        require_bundle_pass=bool(args.prelive_require_bundle_pass),
                    )
                    write_live_pilot_prelive_go_no_go_checklist(
                        prelive_obj,
                        str(args.prelive_go_no_go_report_path),
                    )
                else:
                    prelive_obj = {}
                if str(args.launch_intent_manifest_path or "").strip():
                    launch_intent_obj = build_live_pilot_launch_intent_manifest(
                        mode=str(args.mode or ""),
                        risk_profile_preset=str(args.risk_profile_preset or ""),
                        enable_live_auto_submit_window=bool(args.enable_live_auto_submit_window),
                        adapter_config=(adapter_config if isinstance(adapter_config, dict) else None),
                        prelive_go_no_go_report=prelive_obj,
                        expires_in_seconds=float(args.launch_intent_expires_seconds or 1800.0),
                    )
                    write_live_pilot_launch_intent_manifest(launch_intent_obj, str(args.launch_intent_manifest_path))
                else:
                    launch_intent_obj = {}
                if str(args.promotion_ticket_path or "").strip():
                    write_live_pilot_promotion_ticket(
                        build_live_pilot_promotion_ticket(
                            operator_id=str(args.operator_decision_actor or args.handoff_operator_id or ""),
                            approval_action=str(args.promotion_ticket_action or "approve_live_test"),
                            risk_profile_preset=str(args.risk_profile_preset or ""),
                            promotion_step_manifest=_read_json_or_empty(str(args.promotion_step_manifest_path or "")),
                            prelive_go_no_go_report=prelive_obj,
                            launch_intent_manifest=launch_intent_obj,
                            expires_in_seconds=float(args.promotion_ticket_expires_seconds or 3600.0),
                        ),
                        str(args.promotion_ticket_path),
                    )
                if str(args.postrun_review_packet_path or "").strip():
                    write_live_pilot_postrun_review_packet(
                        build_live_pilot_postrun_review_packet(
                            schedule_report=schedule,
                            daily_operator_report=dict(schedule.get("daily_operator_report") or {}),
                            artifact_index=artifact_index,
                            handoff_snapshot=handoff,
                            bundle_verification=bundle_verification,
                            timeline=timeline_obj,
                        ),
                        str(args.postrun_review_packet_path),
                    )
                if str(args.archive_rotation_glob or "").strip() and str(args.archive_rotation_dir or "").strip():
                    rotation_report = rotate_live_pilot_artifacts_by_glob(
                        glob_pattern=str(args.archive_rotation_glob),
                        archive_dir=str(args.archive_rotation_dir),
                        keep_latest=int(args.archive_rotation_keep or 0),
                    )
                    if str(args.archive_rotation_report_path or "").strip():
                        write_live_pilot_archive_rotation_report(rotation_report, str(args.archive_rotation_report_path))
            cli_out = {
                "schedule_summary": schedule.get("schedule_summary"),
                "report_path": schedule.get("report_path", ""),
                "state_path": schedule.get("state_path", ""),
                "daily_operator_report_path": (str(args.daily_operator_report_path or "")),
                "artifact_index_path": (str(args.artifact_index_path or "")),
                "handoff_snapshot_path": (str(args.handoff_snapshot_path or "")),
                "bundle_verification_report_path": (str(args.bundle_verification_report_path or "")),
                "timeline_export_path": (str(args.timeline_export_path or "")),
                "promotion_step_manifest_path": (str(args.promotion_step_manifest_path or "")),
                "prelive_go_no_go_report_path": (str(args.prelive_go_no_go_report_path or "")),
                "postrun_review_packet_path": (str(args.postrun_review_packet_path or "")),
                "archive_rotation_report_path": (str(args.archive_rotation_report_path or "")),
            }
            print(json.dumps(cli_out, sort_keys=True))
            if bool(args.print_human_summary):
                sessions = list(schedule.get("sessions") or [])
                latest_campaign_summary = dict((sessions[-1] or {}).get("campaign_summary") or {}) if sessions else {}
                aggregate = dict(latest_campaign_summary.get("aggregate_rollup") or {})
                human_in = {
                    "rollup": aggregate,
                    "live_pilot_summary": {},
                    "promotion_gate_summary": dict(latest_campaign_summary.get("promotion_gate_summary") or {}),
                }
                print(_format_human_live_pilot_summary(human_in))
            return 0

        campaign = _run_one_campaign_session()
        if str(args.daily_operator_report_path or "").strip():
            daily_report = build_live_pilot_daily_operator_report(
                [campaign],
                date_label=str(args.daily_operator_date_label or ""),
                recommendation_config=((adapter_config or {}).get("live_pilot_multi_campaign_recommendation") if isinstance(adapter_config, dict) else None),
            )
            if str(args.operator_decision_action or "").strip():
                decision_row = append_live_pilot_operator_decision_log(
                    path_str=str(args.operator_decision_log_jsonl_path or ""),
                    daily_report=daily_report,
                    operator_id=str(args.operator_decision_actor or ""),
                    action=str(args.operator_decision_action or ""),
                    notes=str(args.operator_decision_notes or ""),
                )
                daily_report = apply_operator_acknowledgement_to_daily_report(daily_report, decision_row)
            write_live_pilot_daily_operator_report(daily_report, str(args.daily_operator_report_path))
            if str(args.artifact_index_path or "").strip():
                artifact_index = build_live_pilot_artifact_index(
                    date_label=str(args.daily_operator_date_label or ""),
                    daily_operator_report=daily_report,
                    daily_operator_report_path=str(args.daily_operator_report_path or ""),
                    campaign_reports=[campaign],
                    campaign_report_paths=([str(campaign.get("report_path") or "")] if str(campaign.get("report_path") or "").strip() else []),
                    campaign_state_paths=([str(campaign.get("state_path") or "")] if str(campaign.get("state_path") or "").strip() else []),
                    alerts_jsonl_path=str(args.alerts_jsonl_path or ""),
                    operator_decision_log_jsonl_path=str(args.operator_decision_log_jsonl_path or ""),
                )
                artifact_index.setdefault("artifacts", {})["artifact_index"] = {"path": str(args.artifact_index_path or ""), "present": True}
                write_live_pilot_artifact_index(artifact_index, str(args.artifact_index_path))
                if str(args.handoff_snapshot_path or "").strip():
                    handoff = build_live_pilot_handoff_snapshot(
                        daily_operator_report=daily_report,
                        artifact_index=artifact_index,
                        handoff_operator_id=str(args.handoff_operator_id or args.operator_decision_actor or ""),
                        shift_label=str(args.handoff_shift_label or ""),
                        handoff_notes=str(args.handoff_notes or ""),
                        restart_command_hint=str(args.restart_command_hint or ""),
                    )
                    write_live_pilot_handoff_snapshot(handoff, str(args.handoff_snapshot_path))
                if str(args.bundle_verification_report_path or "").strip():
                    bundle_verification = verify_live_pilot_validation_bundle(artifact_index=artifact_index)
                    write_live_pilot_bundle_verification(bundle_verification, str(args.bundle_verification_report_path))
                else:
                    bundle_verification = verify_live_pilot_validation_bundle(artifact_index=artifact_index)
                if str(args.timeline_export_path or "").strip():
                    timeline_obj = build_live_pilot_session_timeline(
                        schedule_report={"sessions": [{"session_index": 0, "campaign_id": str((campaign.get("campaign_summary") or {}).get("campaign_id") or ""), "campaign_summary": dict(campaign.get("campaign_summary") or {})}]},
                        alerts_rows=_read_jsonl_rows(str(args.alerts_jsonl_path or "")),
                        operator_decision_rows=_read_jsonl_rows(str(args.operator_decision_log_jsonl_path or "")),
                    )
                    write_live_pilot_session_timeline(
                        timeline_obj,
                        str(args.timeline_export_path),
                    )
                else:
                    timeline_obj = {}
                if str(args.promotion_step_manifest_path or "").strip() and str(args.risk_profile_preset or "").strip():
                    write_live_pilot_promotion_step_manifest(
                        build_live_pilot_promotion_step_manifest(
                            risk_profile_preset=str(args.risk_profile_preset),
                            step_name=str(args.promotion_step_name or ""),
                            daily_operator_report=daily_report,
                            artifact_index=artifact_index,
                            bundle_verification=bundle_verification,
                            operator_decision_log_path=str(args.operator_decision_log_jsonl_path or ""),
                        ),
                        str(args.promotion_step_manifest_path),
                    )
                if str(args.prelive_go_no_go_report_path or "").strip():
                    write_live_pilot_prelive_go_no_go_checklist(
                        build_live_pilot_prelive_go_no_go_checklist(
                            daily_operator_report=daily_report,
                            bundle_verification=bundle_verification,
                            handoff_snapshot=handoff,
                            risk_profile_preset=str(args.risk_profile_preset or ""),
                            required_operator_ack=bool(args.prelive_require_operator_ack),
                            require_bundle_pass=bool(args.prelive_require_bundle_pass),
                        ),
                        str(args.prelive_go_no_go_report_path),
                    )
                if str(args.postrun_review_packet_path or "").strip():
                    write_live_pilot_postrun_review_packet(
                        build_live_pilot_postrun_review_packet(
                            daily_operator_report=daily_report,
                            artifact_index=artifact_index,
                            handoff_snapshot=handoff,
                            bundle_verification=bundle_verification,
                            timeline=timeline_obj,
                        ),
                        str(args.postrun_review_packet_path),
                    )
                if str(args.archive_rotation_glob or "").strip() and str(args.archive_rotation_dir or "").strip():
                    rotation_report = rotate_live_pilot_artifacts_by_glob(
                        glob_pattern=str(args.archive_rotation_glob),
                        archive_dir=str(args.archive_rotation_dir),
                        keep_latest=int(args.archive_rotation_keep or 0),
                    )
                    if str(args.archive_rotation_report_path or "").strip():
                        write_live_pilot_archive_rotation_report(rotation_report, str(args.archive_rotation_report_path))
        cli_out = {
            "campaign_summary": campaign.get("campaign_summary"),
            "report_path": campaign.get("report_path", ""),
            "state_path": campaign.get("state_path", ""),
            "daily_operator_report_path": (str(args.daily_operator_report_path or "")),
            "artifact_index_path": (str(args.artifact_index_path or "")),
            "handoff_snapshot_path": (str(args.handoff_snapshot_path or "")),
            "bundle_verification_report_path": (str(args.bundle_verification_report_path or "")),
            "timeline_export_path": (str(args.timeline_export_path or "")),
            "promotion_step_manifest_path": (str(args.promotion_step_manifest_path or "")),
            "prelive_go_no_go_report_path": (str(args.prelive_go_no_go_report_path or "")),
            "postrun_review_packet_path": (str(args.postrun_review_packet_path or "")),
            "archive_rotation_report_path": (str(args.archive_rotation_report_path or "")),
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
