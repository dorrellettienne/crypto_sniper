import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _safe_get(d: dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _build_packet(
    strategy_trace_report: dict,
    strategy_trend_summary: dict,
    scored_discovery_report: dict,
    calibration_trend_summary: dict,
    context: dict,
) -> dict:
    trace = dict(strategy_trace_report.get("strategy_decision_trace") or {})
    guard = dict(strategy_trace_report.get("supervised_entry_promotion_guard") or {})
    trace_summary = dict(trace.get("summary") or {})
    guard_summary = dict(guard.get("summary") or {})
    trend_overall = dict(strategy_trend_summary.get("overall") or {})
    calib_overall = dict(calibration_trend_summary.get("overall") or {})
    scored_summary = dict(scored_discovery_report.get("summary") or {})
    feedback_rec = dict(strategy_trace_report.get("entry_feedback_recommendation") or {})
    feedback_cfg = dict(feedback_rec.get("recommended_entry_rule_config") or {})
    score_band_rec = dict(strategy_trace_report.get("score_band_gate_recommendation") or {})
    exit_policy_rec = dict(strategy_trace_report.get("exit_policy_feedback_recommendation") or {})
    token_mem = dict(strategy_trace_report.get("token_memory_weighted_summary") or {})
    token_mem_agg = dict(token_mem.get("aggregate_weighted") or {})
    packet = {
        "ok": True,
        "report_version": "v1.3_strategy_decision_review_packet_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "trace_candidates_total": int(trace_summary.get("candidates_total", 0) or 0),
            "entry_candidates_enter": int(trace_summary.get("entry_candidates_enter", 0) or 0),
            "entry_candidates_skip": int(trace_summary.get("entry_candidates_skip", 0) or 0),
            "exit_policy_valid": bool(trace_summary.get("exit_policy_valid", False)),
            "guard_candidates_promoted": int(guard_summary.get("candidates_promoted", 0) or 0),
            "guard_promoted_token_addresses": list(guard_summary.get("promoted_token_addresses") or []),
            "strategy_trend_traces_total": int(trend_overall.get("traces_total", 0) or 0),
            "strategy_trend_guard_promoted_total": int(trend_overall.get("guard_promoted_total", 0) or 0),
            "calibration_rows_total": int(calibration_trend_summary.get("rows_total", 0) or 0),
            "calibration_promoted_finalized_rate": _safe_get(calib_overall, "metrics", "promoted_finalized_rate"),
            "calibration_promoted_reconciled_rate": _safe_get(calib_overall, "metrics", "promoted_reconciled_rate"),
            "scored_candidates_total": int(scored_summary.get("candidates_total", 0) or 0),
            "scored_candidates_promoted": int(scored_summary.get("candidates_promoted", 0) or 0),
            "entry_adaptive_enabled": bool(strategy_trace_report.get("entry_rule_adaptive_enabled", False)),
            "entry_recommended_min_score_total": feedback_cfg.get("min_score_total"),
            "entry_recommended_require_probe_ok": feedback_cfg.get("require_probe_ok"),
            "entry_recommendation_reasons": list(feedback_rec.get("recommendation_reasons") or []),
            "score_band_recommended_min_score_total": score_band_rec.get("recommended_min_score_total"),
            "score_band_recommendation_reasons": list(score_band_rec.get("recommendation_reasons") or []),
            "exit_policy_recommended_fingerprint": exit_policy_rec.get("recommended_exit_policy_fingerprint"),
            "exit_policy_recommended_label": exit_policy_rec.get("recommended_exit_policy_label"),
            "exit_policy_recommendation_reasons": list(exit_policy_rec.get("recommendation_reasons") or []),
            "token_memory_promoted_reconciled_rate_weighted": token_mem_agg.get("promoted_reconciled_rate_weighted"),
            "token_memory_promoted_quote_mismatch_rate_weighted": token_mem_agg.get("promoted_quote_mismatch_rate_weighted"),
        },
        "artifacts": {
            "strategy_trace_report_version": str(strategy_trace_report.get("report_version") or ""),
            "strategy_trend_report_version": str(strategy_trend_summary.get("report_version") or ""),
            "scored_discovery_report_version": str(scored_discovery_report.get("report_version") or ""),
            "calibration_trend_report_version": str(calibration_trend_summary.get("report_version") or ""),
        },
        "strategy_decision_trace_report": strategy_trace_report,
        "strategy_decision_trace_trend_summary": strategy_trend_summary,
        "scored_discovery_report": scored_discovery_report,
        "scored_candidate_calibration_trend_summary": calibration_trend_summary,
        "entry_feedback_recommendation": feedback_rec,
        "score_band_gate_recommendation": score_band_rec,
        "exit_policy_feedback_recommendation": exit_policy_rec,
        "context": context,
    }
    return packet


def _to_md(packet: dict) -> str:
    s = dict(packet.get("summary") or {})
    a = dict(packet.get("artifacts") or {})
    lines = [
        "# Strategy Decision Review Packet",
        "",
        f"- generated_at_utc: `{packet.get('generated_at_utc')}`",
        f"- report_version: `{packet.get('report_version')}`",
        "",
        "## Summary",
        "",
        f"- trace_candidates_total: `{s.get('trace_candidates_total')}`",
        f"- entry_candidates_enter: `{s.get('entry_candidates_enter')}`",
        f"- entry_candidates_skip: `{s.get('entry_candidates_skip')}`",
        f"- exit_policy_valid: `{s.get('exit_policy_valid')}`",
        f"- guard_candidates_promoted: `{s.get('guard_candidates_promoted')}`",
        f"- guard_promoted_token_addresses: `{json.dumps(s.get('guard_promoted_token_addresses') or [], separators=(',', ':'))}`",
        f"- strategy_trend_traces_total: `{s.get('strategy_trend_traces_total')}`",
        f"- strategy_trend_guard_promoted_total: `{s.get('strategy_trend_guard_promoted_total')}`",
        f"- calibration_rows_total: `{s.get('calibration_rows_total')}`",
        f"- calibration_promoted_finalized_rate: `{s.get('calibration_promoted_finalized_rate')}`",
        f"- calibration_promoted_reconciled_rate: `{s.get('calibration_promoted_reconciled_rate')}`",
        f"- scored_candidates_total: `{s.get('scored_candidates_total')}`",
        f"- scored_candidates_promoted: `{s.get('scored_candidates_promoted')}`",
        f"- entry_adaptive_enabled: `{s.get('entry_adaptive_enabled')}`",
        f"- entry_recommended_min_score_total: `{s.get('entry_recommended_min_score_total')}`",
        f"- entry_recommended_require_probe_ok: `{s.get('entry_recommended_require_probe_ok')}`",
        f"- entry_recommendation_reasons: `{json.dumps(s.get('entry_recommendation_reasons') or [], separators=(',', ':'))}`",
        f"- score_band_recommended_min_score_total: `{s.get('score_band_recommended_min_score_total')}`",
        f"- score_band_recommendation_reasons: `{json.dumps(s.get('score_band_recommendation_reasons') or [], separators=(',', ':'))}`",
        f"- exit_policy_recommended_fingerprint: `{s.get('exit_policy_recommended_fingerprint')}`",
        f"- exit_policy_recommended_label: `{s.get('exit_policy_recommended_label')}`",
        f"- exit_policy_recommendation_reasons: `{json.dumps(s.get('exit_policy_recommendation_reasons') or [], separators=(',', ':'))}`",
        f"- token_memory_promoted_reconciled_rate_weighted: `{s.get('token_memory_promoted_reconciled_rate_weighted')}`",
        f"- token_memory_promoted_quote_mismatch_rate_weighted: `{s.get('token_memory_promoted_quote_mismatch_rate_weighted')}`",
        "",
        "## Artifact Versions",
        "",
        f"- strategy_trace_report_version: `{a.get('strategy_trace_report_version')}`",
        f"- strategy_trend_report_version: `{a.get('strategy_trend_report_version')}`",
        f"- scored_discovery_report_version: `{a.get('scored_discovery_report_version')}`",
        f"- calibration_trend_report_version: `{a.get('calibration_trend_report_version')}`",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export a V1.3 strategy decision review packet.")
    ap.add_argument("--strategy-trace-json-path", default="data/exports/strategy_decision_trace.json")
    ap.add_argument("--strategy-trend-json-path", default="data/exports/strategy_decision_trace_trend_summary.json")
    ap.add_argument("--scored-discovery-json-path", default="data/exports/scored_discovery_report.json")
    ap.add_argument("--calibration-trend-json-path", default="data/exports/scored_candidate_calibration_trend_summary.json")
    ap.add_argument("--output-json", default="data/exports/strategy_decision_review_packet.json")
    ap.add_argument("--output-md", default="data/exports/strategy_decision_review_packet.md")
    ap.add_argument("--context-run-label", default="")
    args = ap.parse_args()

    strategy_trace_report = _load_json(Path(args.strategy_trace_json_path))
    strategy_trend_summary = _load_json(Path(args.strategy_trend_json_path))
    scored_discovery_report = _load_json(Path(args.scored_discovery_json_path))
    calibration_trend_summary = _load_json(Path(args.calibration_trend_json_path))

    packet = _build_packet(
        strategy_trace_report=strategy_trace_report,
        strategy_trend_summary=strategy_trend_summary,
        scored_discovery_report=scored_discovery_report,
        calibration_trend_summary=calibration_trend_summary,
        context={"run_label": str(args.context_run_label or "").strip()},
    )

    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(packet, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_to_md(packet), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output_json": str(out_json),
                "output_md": str(out_md),
                "guard_candidates_promoted": int(((packet.get("summary") or {}).get("guard_candidates_promoted", 0) or 0)),
                "entry_candidates_enter": int(((packet.get("summary") or {}).get("entry_candidates_enter", 0) or 0)),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
