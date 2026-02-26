import argparse
import json
from pathlib import Path

from src.live.live_pilot_service import build_live_pilot_scored_discovery_report, load_adaptive_reliability_state


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _to_markdown(report: dict) -> str:
    summary = dict(report.get("summary") or {})
    scoring_summary = dict((report.get("scoring") or {}).get("summary") or {})
    promotion_summary = dict((report.get("promotion") or {}).get("summary") or {})
    decisions = list((report.get("promotion") or {}).get("decisions") or [])
    scored = list((report.get("scoring") or {}).get("scored_candidates") or [])
    lines = [
        "# Scored Discovery Report",
        "",
        f"- report_version: `{report.get('report_version')}`",
        f"- candidates_total: `{summary.get('candidates_total', 0)}`",
        f"- candidates_eligible: `{summary.get('candidates_eligible', 0)}`",
        f"- candidates_promoted: `{summary.get('candidates_promoted', 0)}`",
        f"- promoted_token_addresses: `{json.dumps(summary.get('promoted_token_addresses', []), separators=(',', ':'))}`",
        "",
        "## Scoring Summary",
        "",
        f"- reject_reason_counts: `{json.dumps(scoring_summary.get('reject_reason_counts', {}), separators=(',', ':'))}`",
        "",
        "## Promotion Summary",
        "",
        f"- max_candidates: `{promotion_summary.get('max_candidates')}`",
        f"- min_score_total: `{promotion_summary.get('min_score_total')}`",
        f"- require_probe_ok: `{promotion_summary.get('require_probe_ok')}`",
        "",
        "## Top Ranked Candidates",
        "",
    ]
    for row in scored[:10]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- `{row.get('token_address')}` `{row.get('symbol')}` score=`{row.get('score_total')}` eligible=`{row.get('eligible')}` rejects=`{json.dumps(row.get('reject_reasons', []), separators=(',', ':'))}`"
        )
    lines.append("")
    lines.append("## Promotion Decisions")
    lines.append("")
    for row in decisions[:20]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- `{row.get('token_address')}` promoted=`{row.get('promoted')}` reasons=`{json.dumps(row.get('decision_reasons', []), separators=(',', ':'))}`"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Build a V1.2 scored discovery report from candidate JSON (no-send ranking only).")
    p.add_argument("--candidate-json-path", required=True)
    p.add_argument("--adaptive-reliability-state-json-path", default="")
    p.add_argument("--output-json", default="data/exports/scored_discovery_report.json")
    p.add_argument("--output-md", default="data/exports/scored_discovery_report.md")
    p.add_argument("--min-liquidity-usd", type=float, default=1000.0)
    p.add_argument("--max-pair-age-seconds", type=float, default=86400.0)
    p.add_argument("--min-volume-5m-usd", type=float, default=0.0)
    p.add_argument("--max-abs-price-change-5m-pct", type=float, default=80.0)
    p.add_argument("--promote-max-candidates", type=int, default=1)
    p.add_argument("--promote-min-score-total", type=float, default=0.0)
    p.add_argument("--promote-require-probe-ok", action="store_true")
    args = p.parse_args()

    candidates = _load_json(Path(args.candidate_json_path))
    if not isinstance(candidates, list):
        raise SystemExit("candidate_json_must_be_list")
    reliability_state = load_adaptive_reliability_state(str(args.adaptive_reliability_state_json_path or ""))
    report = build_live_pilot_scored_discovery_report(
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

    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    Path(args.output_md).write_text(_to_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output_json": args.output_json,
                "output_md": args.output_md,
                "candidates_total": ((report.get("summary") or {}).get("candidates_total", 0)),
                "candidates_promoted": ((report.get("summary") or {}).get("candidates_promoted", 0)),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
