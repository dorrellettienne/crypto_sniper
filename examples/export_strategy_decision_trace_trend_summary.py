import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _load_jsonl(path: Path) -> list[dict]:
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
        return str(((row.get("context") or {}).get("run_label") or "unlabeled"))
    ts = str(row.get("timestamp_utc") or "")
    return ts[:10] if ts else "unknown_date"


def _summarize(rows: list[dict]) -> dict:
    traces_total = 0
    candidates_total = 0
    enter_total = 0
    skip_total = 0
    guard_promoted_total = 0
    exit_policy_invalid_total = 0
    confidence_counts: dict[str, int] = {}
    guard_reject_reason_counts: dict[str, int] = {}
    for row in rows:
        if str(row.get("event_type") or "") != "live_pilot_strategy_decision_trace":
            continue
        payload = dict(row.get("payload") or {})
        trace = dict(payload.get("strategy_decision_trace") or {})
        guard = dict(payload.get("supervised_entry_promotion_guard") or {})
        traces_total += 1
        t_summary = dict(trace.get("summary") or {})
        g_summary = dict(guard.get("summary") or {})
        candidates_total += int(t_summary.get("candidates_total", 0) or 0)
        enter_total += int(t_summary.get("entry_candidates_enter", 0) or 0)
        skip_total += int(t_summary.get("entry_candidates_skip", 0) or 0)
        guard_promoted_total += int(g_summary.get("candidates_promoted", 0) or 0)
        if not bool(t_summary.get("exit_policy_valid", False)):
            exit_policy_invalid_total += 1
        for d in list(trace.get("decision_trace") or []):
            if not isinstance(d, dict):
                continue
            conf = str(d.get("confidence") or "")
            if conf:
                confidence_counts[conf] = int(confidence_counts.get(conf, 0)) + 1
        for k, v in dict(g_summary.get("reject_reason_counts") or {}).items():
            guard_reject_reason_counts[str(k)] = int(guard_reject_reason_counts.get(str(k), 0)) + int(v or 0)
    return {
        "traces_total": traces_total,
        "candidates_total": candidates_total,
        "enter_total": enter_total,
        "skip_total": skip_total,
        "guard_promoted_total": guard_promoted_total,
        "exit_policy_invalid_total": exit_policy_invalid_total,
        "counts": {
            "confidence": confidence_counts,
            "guard_reject_reason_counts": guard_reject_reason_counts,
        },
    }


def _md(report: dict) -> str:
    overall = dict(report.get("overall") or {})
    counts = dict(overall.get("counts") or {})
    periods = list(report.get("periods") or [])
    lines = [
        "# Strategy Decision Trace Trend Summary",
        "",
        f"- generated_at_utc: `{report.get('generated_at_utc')}`",
        f"- group_by: `{report.get('group_by')}`",
        f"- rows_total: `{report.get('rows_total', 0)}`",
        f"- periods_total: `{len(periods)}`",
        "",
        "## Overall",
        "",
        f"- traces_total: `{overall.get('traces_total', 0)}`",
        f"- candidates_total: `{overall.get('candidates_total', 0)}`",
        f"- enter_total: `{overall.get('enter_total', 0)}`",
        f"- skip_total: `{overall.get('skip_total', 0)}`",
        f"- guard_promoted_total: `{overall.get('guard_promoted_total', 0)}`",
        f"- exit_policy_invalid_total: `{overall.get('exit_policy_invalid_total', 0)}`",
        f"- confidence_counts: `{json.dumps(counts.get('confidence') or {}, separators=(',', ':'))}`",
        f"- guard_reject_reason_counts: `{json.dumps(counts.get('guard_reject_reason_counts') or {}, separators=(',', ':'))}`",
        "",
        "## Periods",
        "",
    ]
    if not periods:
        lines.append("_No strategy trace rows found._")
    else:
        for p in periods:
            if not isinstance(p, dict):
                continue
            s = dict(p.get("summary") or {})
            c = dict(s.get("counts") or {})
            lines.append(
                f"- `{p.get('period')}` traces=`{s.get('traces_total',0)}` candidates=`{s.get('candidates_total',0)}` "
                f"enter=`{s.get('enter_total',0)}` promoted=`{s.get('guard_promoted_total',0)}` "
                f"exit_policy_invalid=`{s.get('exit_policy_invalid_total',0)}` rejects=`{json.dumps(c.get('guard_reject_reason_counts') or {}, separators=(',', ':'))}`"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export trend summary over strategy decision trace JSONL rows.")
    ap.add_argument("--strategy-trace-log-jsonl-path", default="data/exports/strategy_decision_traces.jsonl")
    ap.add_argument("--output-json", default="data/exports/strategy_decision_trace_trend_summary.json")
    ap.add_argument("--output-md", default="data/exports/strategy_decision_trace_trend_summary.md")
    ap.add_argument("--group-by", choices=["date", "run_label"], default="date")
    args = ap.parse_args()

    rows = _load_jsonl(Path(args.strategy_trace_log_jsonl_path))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[_period_key(row, str(args.group_by))].append(row)

    periods = []
    for key in sorted(grouped.keys()):
        periods.append({"period": key, "rows_total": len(grouped[key]), "summary": _summarize(grouped[key])})

    report = {
        "ok": True,
        "report_version": "v1.3_strategy_decision_trace_trend_summary_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "group_by": str(args.group_by),
        "rows_total": len(rows),
        "overall": _summarize(rows),
        "periods": periods,
    }
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_md(report), encoding="utf-8")
    print(json.dumps({"ok": True, "output_json": str(out_json), "output_md": str(out_md), "rows_total": len(rows), "periods_total": len(periods)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
