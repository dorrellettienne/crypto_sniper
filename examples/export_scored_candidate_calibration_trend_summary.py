import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.live.live_pilot_service import build_live_pilot_scored_candidate_calibration_summary


def _load_jsonl_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
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


def _period_key(row: dict, group_by: str) -> str:
    if group_by == "run_label":
        ctx = dict(row.get("context") or {})
        return str(ctx.get("run_label") or "unlabeled")
    ts = str(row.get("timestamp_utc") or "")
    if ts:
        return ts[:10]
    return "unknown_date"


def _md(report: dict) -> str:
    overall = dict(report.get("overall") or {})
    om = dict(overall.get("metrics") or {})
    periods = list(report.get("periods") or [])
    lines = [
        "# Scored Candidate Calibration Trend Summary",
        "",
        f"- generated_at_utc: `{report.get('generated_at_utc')}`",
        f"- group_by: `{report.get('group_by')}`",
        f"- rows_total: `{report.get('rows_total', 0)}`",
        f"- periods_total: `{len(periods)}`",
        "",
        "## Overall Metrics",
        "",
        f"- promoted_count: `{om.get('promoted_count')}`",
        f"- finalized_count: `{om.get('finalized_count')}`",
        f"- reconciled_count: `{om.get('reconciled_count')}`",
        f"- truth_complete_count: `{om.get('truth_complete_count')}`",
        f"- promoted_finalized_rate: `{om.get('promoted_finalized_rate')}`",
        f"- promoted_reconciled_rate: `{om.get('promoted_reconciled_rate')}`",
        f"- avg_slippage_bps_all: `{om.get('avg_slippage_bps_all')}`",
        f"- worst_slippage_bps_all: `{om.get('worst_slippage_bps_all')}`",
        "",
        "## Periods",
        "",
    ]
    if not periods:
        lines.append("_No rows found._")
    else:
        for p in periods:
            if not isinstance(p, dict):
                continue
            metrics = dict((p.get("summary") or {}).get("metrics") or {})
            counts = dict((p.get("summary") or {}).get("counts") or {})
            lines.append(
                f"- `{p.get('period')}` rows=`{p.get('rows_total', 0)}` promoted=`{metrics.get('promoted_count')}` "
                f"finalized=`{metrics.get('finalized_count')}` reconciled=`{metrics.get('reconciled_count')}` "
                f"truth_complete=`{metrics.get('truth_complete_count')}` mismatch_classes=`{json.dumps(counts.get('quote_mismatch_class') or {}, separators=(',', ':'))}`"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export a trend summary over scored candidate outcome calibration JSONL rows.")
    ap.add_argument("--outcome-log-jsonl-path", default="data/exports/scored_candidate_outcomes.jsonl")
    ap.add_argument("--output-json", default="data/exports/scored_candidate_calibration_trend_summary.json")
    ap.add_argument("--output-md", default="data/exports/scored_candidate_calibration_trend_summary.md")
    ap.add_argument("--group-by", choices=["date", "run_label"], default="date")
    args = ap.parse_args()

    rows = _load_jsonl_rows(Path(args.outcome_log_jsonl_path))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[_period_key(row, str(args.group_by))].append(row)

    periods = []
    for key in sorted(grouped.keys()):
        summary = build_live_pilot_scored_candidate_calibration_summary(grouped[key])
        periods.append(
            {
                "period": key,
                "rows_total": int(summary.get("rows_total", 0) or 0),
                "summary": summary,
            }
        )

    overall = build_live_pilot_scored_candidate_calibration_summary(rows)
    report = {
        "ok": True,
        "report_version": "v1.2_scored_candidate_calibration_trend_summary_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "group_by": str(args.group_by),
        "rows_total": len(rows),
        "overall": overall,
        "periods": periods,
    }

    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_md(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output_json": str(out_json),
                "output_md": str(out_md),
                "rows_total": len(rows),
                "periods_total": len(periods),
                "group_by": str(args.group_by),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
