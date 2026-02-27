import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _to_float_or_none(v: Any) -> float | None:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except Exception:
        return None


def build_gate(summary: dict[str, Any], *, min_finalized: int, max_mismatch_rate: float, max_avg_slippage_bps: float, max_no_finalized_streak: int) -> dict[str, Any]:
    m = dict(summary.get("metrics") or {})
    runs = list(summary.get("recent_runs") or [])
    finalized_count = int(m.get("finalized_count", 0) or 0)
    mismatch_rate = _to_float_or_none(m.get("quote_mismatch_rate"))
    avg_slippage = _to_float_or_none(m.get("avg_realized_slippage_bps"))

    no_finalized_streak = 0
    for row in runs:
        if str((row or {}).get("confirmation_status") or "") == "finalized":
            break
        no_finalized_streak += 1

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, actual: Any, expected: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "actual": actual, "expected": expected})

    add("min_finalized_count", finalized_count >= int(min_finalized), finalized_count, f">= {int(min_finalized)}")
    add("max_quote_mismatch_rate", (mismatch_rate is not None and mismatch_rate <= float(max_mismatch_rate)), mismatch_rate, f"<= {float(max_mismatch_rate)}")
    add("max_avg_realized_slippage_bps", (avg_slippage is not None and avg_slippage <= float(max_avg_slippage_bps)), avg_slippage, f"<= {float(max_avg_slippage_bps)}")
    add("max_no_finalized_streak", no_finalized_streak <= int(max_no_finalized_streak), no_finalized_streak, f"<= {int(max_no_finalized_streak)}")

    failed_checks = [str(c.get("name") or "") for c in checks if not bool(c.get("ok", False))]
    go = len(failed_checks) == 0
    return {
        "ok": True,
        "report_version": "v1.6_go_no_go_gate_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "go": go,
        "summary": ("go" if go else ("no_go:" + ",".join(failed_checks))),
        "failed_checks": failed_checks,
        "checks": checks,
        "inputs": {
            "v16_performance_summary_path": "data/exports/v16_performance_summary.json",
            "min_finalized": int(min_finalized),
            "max_mismatch_rate": float(max_mismatch_rate),
            "max_avg_slippage_bps": float(max_avg_slippage_bps),
            "max_no_finalized_streak": int(max_no_finalized_streak),
        },
    }


def _to_md(report: dict[str, Any]) -> str:
    lines = [
        "# V1.6 Go/No-Go Gate",
        "",
        f"- generated_at_utc: `{report.get('generated_at_utc')}`",
        f"- go: `{report.get('go')}`",
        f"- summary: `{report.get('summary')}`",
        f"- failed_checks: `{json.dumps(report.get('failed_checks') or [], separators=(',', ':'))}`",
        "",
        "## Checks",
        "",
    ]
    for c in list(report.get("checks") or []):
        lines.append(f"- `{c.get('name')}` ok=`{c.get('ok')}` actual=`{json.dumps(c.get('actual'), separators=(',', ':'))}` expected=`{c.get('expected')}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export V1.6 go/no-go gate from performance summary.")
    ap.add_argument("--v16-performance-summary-json-path", default="data/exports/v16_performance_summary.json")
    ap.add_argument("--min-finalized", type=int, default=3)
    ap.add_argument("--max-mismatch-rate", type=float, default=0.50)
    ap.add_argument("--max-avg-slippage-bps", type=float, default=50.0)
    ap.add_argument("--max-no-finalized-streak", type=int, default=3)
    ap.add_argument("--output-json", default="data/exports/v16_go_no_go_gate.json")
    ap.add_argument("--output-md", default="data/exports/v16_go_no_go_gate.md")
    args = ap.parse_args()

    summary = _read_json(Path(args.v16_performance_summary_json_path))
    report = build_gate(
        summary,
        min_finalized=int(args.min_finalized),
        max_mismatch_rate=float(args.max_mismatch_rate),
        max_avg_slippage_bps=float(args.max_avg_slippage_bps),
        max_no_finalized_streak=int(args.max_no_finalized_streak),
    )

    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_to_md(report), encoding="utf-8")
    print(
        json.dumps(
            {"ok": True, "output_json": str(out_json), "output_md": str(out_md), "go": report.get("go", False), "failed_checks": report.get("failed_checks", [])},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

