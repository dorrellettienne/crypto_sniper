import argparse
import hashlib
import json
from pathlib import Path

from src.live.live_pilot_service import (
    build_live_pilot_entry_rule_decisions,
    build_live_pilot_exit_policy_schema,
    build_live_pilot_exit_policy_outcome_correlation_summary,
    build_live_pilot_score_band_outcome_summary,
    build_live_pilot_strategy_decision_trace,
    build_live_pilot_token_memory_weighted_outcome_summary,
    evaluate_live_pilot_supervised_entry_promotion_guard,
    load_adaptive_reliability_state,
    recommend_live_pilot_entry_rule_config_from_feedback,
    validate_live_pilot_exit_policy_schema,
    build_live_pilot_scored_candidate_calibration_summary,
    recommend_live_pilot_exit_policy_fingerprint_from_outcomes,
    recommend_live_pilot_score_band_gate_from_outcomes,
)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl_rows(path: Path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _to_float_or_none(v):
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _to_md(trace: dict, guard: dict) -> str:
    ts = dict(trace.get("summary") or {})
    gs = dict(guard.get("summary") or {})
    rows = list(trace.get("decision_trace") or [])
    gdec = list(guard.get("decisions") or [])
    gmap = {str(d.get("token_address") or ""): dict(d) for d in gdec if isinstance(d, dict)}
    lines = [
        "# Strategy Decision Trace",
        "",
        f"- trace_version: `{trace.get('trace_version')}`",
        f"- guard_version: `{guard.get('guard_version')}`",
        f"- candidates_total: `{ts.get('candidates_total')}`",
        f"- entry_candidates_enter: `{ts.get('entry_candidates_enter')}`",
        f"- exit_policy_valid: `{ts.get('exit_policy_valid')}`",
        f"- guard_candidates_promoted: `{gs.get('candidates_promoted')}`",
        f"- promoted_token_addresses: `{json.dumps(gs.get('promoted_token_addresses', []), separators=(',', ':'))}`",
        "",
        "## Guard Summary",
        "",
        f"- require_exit_policy_valid: `{gs.get('require_exit_policy_valid')}`",
        f"- require_entry_decision_enter: `{gs.get('require_entry_decision_enter')}`",
        f"- require_confidence_at_least: `{gs.get('require_confidence_at_least')}`",
        f"- max_candidates: `{gs.get('max_candidates')}`",
        f"- reject_reason_counts: `{json.dumps(gs.get('reject_reason_counts') or {}, separators=(',', ':'))}`",
        "",
        "## Candidate Decisions",
        "",
    ]
    if not rows:
        lines.append("_No decision rows found._")
    for r in rows:
        if not isinstance(r, dict):
            continue
        tok = str(r.get("token_address") or "")
        gd = gmap.get(tok, {})
        lines.append(
            f"- `{tok}` `{r.get('symbol')}` entry=`{r.get('decision')}` conf=`{r.get('confidence')}` "
            f"guard_promoted=`{gd.get('promoted')}` entry_reasons=`{json.dumps(r.get('entry_decision_reasons') or [], separators=(',', ':'))}` "
            f"guard_reasons=`{json.dumps(gd.get('decision_reasons') or [], separators=(',', ':'))}`"
        )
    return "\n".join(lines) + "\n"


def _schema_fingerprint(schema: dict) -> str:
    raw = json.dumps(dict(schema or {}), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _exit_policy_catalog() -> list[dict]:
    # Minimal built-in policy variants for adaptive selection by past outcome fingerprint.
    return [
        build_live_pilot_exit_policy_schema(policy_config={"notes": "catalog_default"}),
        build_live_pilot_exit_policy_schema(
            policy_config={
                "take_profit_bps": 120.0,
                "stop_loss_bps": 80.0,
                "max_hold_seconds": 240,
                "trailing_stop": {"enabled": True, "activation_bps": 110.0, "trail_bps": 55.0},
                "notes": "catalog_tighter_bracket",
            }
        ),
        build_live_pilot_exit_policy_schema(
            policy_config={
                "take_profit_bps": 220.0,
                "stop_loss_bps": 120.0,
                "max_hold_seconds": 420,
                "trailing_stop": {"enabled": True, "activation_bps": 150.0, "trail_bps": 75.0},
                "notes": "catalog_wider_trend_hold",
            }
        ),
    ]


def main() -> int:
    p = argparse.ArgumentParser(description="Build V1.3 strategy decision trace + supervised entry promotion guard report.")
    p.add_argument("--candidate-json-path", required=True)
    p.add_argument("--adaptive-reliability-state-json-path", default="")
    p.add_argument("--outcome-log-jsonl-path", default="data/exports/scored_candidate_outcomes.jsonl")
    p.add_argument("--output-json", default="data/exports/strategy_decision_trace.json")
    p.add_argument("--output-md", default="data/exports/strategy_decision_trace.md")
    p.add_argument("--min-liquidity-usd", type=float, default=1000.0)
    p.add_argument("--max-pair-age-seconds", type=float, default=86400.0)
    p.add_argument("--min-volume-5m-usd", type=float, default=0.0)
    p.add_argument("--max-abs-price-change-5m-pct", type=float, default=80.0)
    p.add_argument("--promote-max-candidates", type=int, default=1)
    p.add_argument("--promote-min-score-total", type=float, default=0.0)
    p.add_argument("--promote-require-probe-ok", action="store_true")
    p.add_argument("--entry-min-score-total", type=float, default=10.0)
    p.add_argument("--entry-require-probe-ok", action="store_true")
    p.add_argument("--entry-max-quote-mismatch-rate", type=float, default=None)
    p.add_argument("--entry-min-promoted-reconciled-rate", type=float, default=None)
    p.add_argument("--entry-adaptive-from-feedback", action="store_true")
    p.add_argument("--entry-feedback-min-history-rows", type=int, default=10)
    p.add_argument("--entry-feedback-max-promoted-quote-mismatch-rate", type=float, default=0.30)
    p.add_argument("--entry-feedback-min-promoted-reconciled-rate", type=float, default=0.65)
    p.add_argument("--entry-feedback-raise-min-score-step", type=float, default=5.0)
    p.add_argument("--exit-policy-adaptive-from-feedback", action="store_true")
    p.add_argument("--guard-max-candidates", type=int, default=1)
    p.add_argument("--guard-require-confidence-at-least", choices=["low", "medium", "high"], default="medium")
    p.add_argument("--exit-policy-json-path", default="")
    args = p.parse_args()

    candidates = _load_json(Path(args.candidate_json_path))
    if not isinstance(candidates, list):
        raise SystemExit("candidate_json_must_be_list")

    reliability_state = load_adaptive_reliability_state(str(args.adaptive_reliability_state_json_path or ""))
    from src.live.live_pilot_service import build_live_pilot_scored_discovery_report  # local import to keep script simple

    scored_report = build_live_pilot_scored_discovery_report(
        candidates,
        reliability_state=reliability_state,
        scoring_config={
            "min_liquidity_usd": float(args.min_liquidity_usd),
            "max_pair_age_seconds": float(args.max_pair_age_seconds),
            "min_volume_5m_usd": float(args.min_volume_5m_usd),
            "max_abs_price_change_5m_pct": float(args.max_abs_price_change_5m_pct),
        },
        promotion_config={
            "max_candidates": int(args.promote_max_candidates),
            "min_score_total": float(args.promote_min_score_total),
            "require_probe_ok": bool(args.promote_require_probe_ok),
        },
    )

    calibration_rows = _load_jsonl_rows(Path(args.outcome_log_jsonl_path))
    calibration_summary = build_live_pilot_scored_candidate_calibration_summary(calibration_rows)
    score_band_outcomes = build_live_pilot_score_band_outcome_summary(calibration_rows)
    token_memory_weighted_summary = build_live_pilot_token_memory_weighted_outcome_summary(calibration_rows)
    exit_policy_outcomes = build_live_pilot_exit_policy_outcome_correlation_summary(calibration_rows)
    base_entry_rule_config = {
        "min_score_total": float(args.entry_min_score_total),
        "require_probe_ok": bool(args.entry_require_probe_ok),
        "max_quote_mismatch_rate": args.entry_max_quote_mismatch_rate,
        "min_promoted_reconciled_rate": args.entry_min_promoted_reconciled_rate,
    }
    score_band_gate_recommendation = recommend_live_pilot_score_band_gate_from_outcomes(
        score_band_outcomes,
        base_min_score_total=float(base_entry_rule_config.get("min_score_total", 0.0) or 0.0),
    )
    exit_policy_feedback_recommendation = recommend_live_pilot_exit_policy_fingerprint_from_outcomes(exit_policy_outcomes)
    adaptive_entry_recommendation = recommend_live_pilot_entry_rule_config_from_feedback(
        calibration_summary=calibration_summary,
        score_band_outcome_summary=score_band_outcomes,
        token_memory_weighted_summary=token_memory_weighted_summary,
        base_entry_rule_config=base_entry_rule_config,
        policy_config={
            "min_history_rows": int(args.entry_feedback_min_history_rows),
            "max_promoted_quote_mismatch_rate": float(args.entry_feedback_max_promoted_quote_mismatch_rate),
            "min_promoted_reconciled_rate": float(args.entry_feedback_min_promoted_reconciled_rate),
            "raise_min_score_step": float(args.entry_feedback_raise_min_score_step),
        },
    )
    effective_entry_rule_config = dict(base_entry_rule_config)
    if bool(args.entry_adaptive_from_feedback):
        effective_entry_rule_config = dict(adaptive_entry_recommendation.get("recommended_entry_rule_config") or effective_entry_rule_config)
        sb_floor = _to_float_or_none(score_band_gate_recommendation.get("recommended_min_score_total"))
        if sb_floor is not None:
            effective_entry_rule_config["min_score_total"] = round(
                max(float(_to_float_or_none(effective_entry_rule_config.get("min_score_total")) or 0.0), float(sb_floor)),
                6,
            )
    entry_decisions = build_live_pilot_entry_rule_decisions(
        scored_report,
        entry_rule_config=effective_entry_rule_config,
        calibration_summary=calibration_summary,
    )

    if str(args.exit_policy_json_path or "").strip():
        exit_policy = _load_json(Path(args.exit_policy_json_path))
    else:
        exit_policy = build_live_pilot_exit_policy_schema()
    exit_policy_selected_from_feedback = False
    if bool(args.exit_policy_adaptive_from_feedback) and not str(args.exit_policy_json_path or "").strip():
        rec_fp = str(exit_policy_feedback_recommendation.get("recommended_exit_policy_fingerprint") or "")
        if rec_fp:
            for candidate in _exit_policy_catalog():
                if _schema_fingerprint(candidate) == rec_fp:
                    exit_policy = dict(candidate)
                    exit_policy_selected_from_feedback = True
                    break
    exit_policy_valid = validate_live_pilot_exit_policy_schema(exit_policy)

    trace = build_live_pilot_strategy_decision_trace(
        scored_discovery_report=scored_report,
        entry_rule_decisions=entry_decisions,
        exit_policy_schema=exit_policy,
        exit_policy_validation=exit_policy_valid,
        context={
            "candidate_json_path": str(args.candidate_json_path),
            "outcome_log_jsonl_path": str(args.outcome_log_jsonl_path),
            "exit_policy_selected_from_feedback": bool(exit_policy_selected_from_feedback),
        },
    )
    guard = evaluate_live_pilot_supervised_entry_promotion_guard(
        trace,
        guard_config={
            "max_candidates": int(args.guard_max_candidates),
            "require_confidence_at_least": str(args.guard_require_confidence_at_least),
        },
    )

    report = {
        "report_version": "v1.3_strategy_decision_trace_report_v1",
        "scored_discovery_report": scored_report,
        "calibration_summary": calibration_summary,
        "score_band_outcome_summary": score_band_outcomes,
        "token_memory_weighted_summary": token_memory_weighted_summary,
        "score_band_gate_recommendation": score_band_gate_recommendation,
        "exit_policy_outcome_correlation_summary": exit_policy_outcomes,
        "exit_policy_feedback_recommendation": exit_policy_feedback_recommendation,
        "exit_policy_adaptive_enabled": bool(args.exit_policy_adaptive_from_feedback),
        "exit_policy_selected_from_feedback": bool(exit_policy_selected_from_feedback),
        "entry_feedback_recommendation": adaptive_entry_recommendation,
        "entry_rule_config_base": base_entry_rule_config,
        "entry_rule_config_effective": effective_entry_rule_config,
        "entry_rule_adaptive_enabled": bool(args.entry_adaptive_from_feedback),
        "entry_rule_decisions": entry_decisions,
        "strategy_decision_trace": trace,
        "supervised_entry_promotion_guard": guard,
    }
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_to_md(trace, guard), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output_json": str(out_json),
                "output_md": str(out_md),
                "candidates_total": int(((trace.get("summary") or {}).get("candidates_total", 0) or 0)),
                "entry_candidates_enter": int(((trace.get("summary") or {}).get("entry_candidates_enter", 0) or 0)),
                "guard_candidates_promoted": int(((guard.get("summary") or {}).get("candidates_promoted", 0) or 0)),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
