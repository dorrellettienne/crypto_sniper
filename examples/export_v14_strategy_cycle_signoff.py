import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
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


def _build_summary(rows: list[dict], required_finalized_cycles: int) -> dict:
    total = len(rows)
    finalized_rows = [r for r in rows if bool(r.get("ok")) and str(r.get("confirmation_status") or "") == "finalized" and bool(r.get("tx_present"))]
    failed_rows = [r for r in rows if not bool(r.get("ok"))]
    failure_class_counts: dict[str, int] = {}
    for r in failed_rows:
        k = str(r.get("failure_class") or "unknown_failure")
        failure_class_counts[k] = int(failure_class_counts.get(k, 0)) + 1
    signatures = [str(r.get("signature") or "") for r in finalized_rows if str(r.get("signature") or "")]
    unique_sigs = list(dict.fromkeys(signatures))
    return {
        "ok": True,
        "report_version": "v1.4_strategy_cycle_signoff_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "required_finalized_cycles": int(required_finalized_cycles),
        "cycles_total": int(total),
        "cycles_finalized_ok": int(len(finalized_rows)),
        "cycles_failed": int(len(failed_rows)),
        "failure_class_counts": failure_class_counts,
        "unique_finalized_signatures": unique_sigs,
        "latest_cycle": (rows[-1] if rows else {}),
        "signoff_ready": bool(len(finalized_rows) >= int(required_finalized_cycles)),
    }


def _to_md(summary: dict) -> str:
    latest = dict(summary.get("latest_cycle") or {})
    lines = [
        "# V1.4 Strategy Cycle Signoff",
        "",
        f"- generated_at_utc: `{summary.get('generated_at_utc')}`",
        f"- required_finalized_cycles: `{summary.get('required_finalized_cycles')}`",
        f"- cycles_total: `{summary.get('cycles_total')}`",
        f"- cycles_finalized_ok: `{summary.get('cycles_finalized_ok')}`",
        f"- cycles_failed: `{summary.get('cycles_failed')}`",
        f"- failure_class_counts: `{json.dumps(summary.get('failure_class_counts') or {}, separators=(',', ':'))}`",
        f"- signoff_ready: `{summary.get('signoff_ready')}`",
        f"- unique_finalized_signatures: `{json.dumps(summary.get('unique_finalized_signatures') or [], separators=(',', ':'))}`",
        "",
        "## Latest Cycle",
        "",
        f"- cycle_id: `{latest.get('cycle_id')}`",
        f"- confirmation_status: `{latest.get('confirmation_status')}`",
        f"- tx_present: `{latest.get('tx_present')}`",
        f"- signature: `{latest.get('signature')}`",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export V1.4 strategy cycle signoff summary from JSONL cycle log.")
    ap.add_argument("--cycle-log-jsonl-path", default="data/exports/v14_strategy_cycle_log.jsonl")
    ap.add_argument("--required-finalized-cycles", type=int, default=3)
    ap.add_argument("--output-json", default="data/exports/v14_strategy_cycle_signoff.json")
    ap.add_argument("--output-md", default="data/exports/v14_strategy_cycle_signoff.md")
    args = ap.parse_args()

    rows = _read_jsonl(Path(args.cycle_log_jsonl_path))
    summary = _build_summary(rows, int(args.required_finalized_cycles))
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_to_md(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output_json": str(out_json),
                "output_md": str(out_md),
                "cycles_total": summary.get("cycles_total", 0),
                "cycles_finalized_ok": summary.get("cycles_finalized_ok", 0),
                "signoff_ready": summary.get("signoff_ready", False),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
