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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def build_readiness(
    release_checkpoint: dict[str, Any],
    cycle_rows: list[dict[str, Any]],
    *,
    min_cycles_total: int = 7,
    recent_cycles_required: int = 3,
) -> dict[str, Any]:
    rows = [dict(r) for r in cycle_rows if isinstance(r, dict)]
    total = len(rows)
    finalized_ok = [
        r
        for r in rows
        if bool(r.get("ok"))
        and str(r.get("confirmation_status") or "") == "finalized"
        and bool(r.get("tx_present"))
    ]
    cycles_failed = max(0, total - len(finalized_ok))
    unique_sigs = list(dict.fromkeys([str(r.get("signature") or "") for r in finalized_ok if str(r.get("signature") or "")]))
    recent = rows[-recent_cycles_required:] if recent_cycles_required > 0 else rows
    recent_all_ok = (
        len(recent) == recent_cycles_required
        and all(bool(r.get("ok")) and str(r.get("confirmation_status") or "") == "finalized" and bool(r.get("tx_present")) for r in recent)
    )

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, actual: Any, expected: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "actual": actual, "expected": expected})

    release_ready = bool(release_checkpoint.get("release_ready", False))
    add("v14_release_ready", release_ready, release_ready, True)
    add("cycles_total_min", total >= int(min_cycles_total), total, f">= {int(min_cycles_total)}")
    add("cycles_failed_zero", cycles_failed == 0, cycles_failed, 0)
    add("recent_cycles_all_finalized_ok", recent_all_ok, recent_all_ok, True)

    failed_checks = [str(c.get("name") or "") for c in checks if not bool(c.get("ok", False))]
    ready = len(failed_checks) == 0
    return {
        "ok": True,
        "report_version": "v1.5_entry_readiness_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "entry_ready": ready,
        "summary": ("entry_ready" if ready else ("entry_blocked:" + ",".join(failed_checks))),
        "failed_checks": failed_checks,
        "checks": checks,
        "metrics": {
            "cycles_total": total,
            "cycles_finalized_ok": len(finalized_ok),
            "cycles_failed": cycles_failed,
            "unique_finalized_signature_count": len(unique_sigs),
            "recent_cycles_required": int(recent_cycles_required),
            "recent_cycles_evaluated": len(recent),
            "recent_cycles_all_finalized_ok": recent_all_ok,
        },
    }


def _to_md(report: dict[str, Any]) -> str:
    m = dict(report.get("metrics") or {})
    lines = [
        "# V1.5 Entry Readiness",
        "",
        f"- generated_at_utc: `{report.get('generated_at_utc')}`",
        f"- entry_ready: `{report.get('entry_ready')}`",
        f"- summary: `{report.get('summary')}`",
        f"- failed_checks: `{json.dumps(report.get('failed_checks') or [], separators=(',', ':'))}`",
        "",
        "## Metrics",
        "",
        f"- cycles_total: `{m.get('cycles_total')}`",
        f"- cycles_finalized_ok: `{m.get('cycles_finalized_ok')}`",
        f"- cycles_failed: `{m.get('cycles_failed')}`",
        f"- unique_finalized_signature_count: `{m.get('unique_finalized_signature_count')}`",
        f"- recent_cycles_required: `{m.get('recent_cycles_required')}`",
        f"- recent_cycles_evaluated: `{m.get('recent_cycles_evaluated')}`",
        f"- recent_cycles_all_finalized_ok: `{m.get('recent_cycles_all_finalized_ok')}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export V1.5 entry readiness report from V1.4 supervised ops artifacts.")
    ap.add_argument("--release-checkpoint-json-path", default="data/exports/v14_release_checkpoint.json")
    ap.add_argument("--cycle-log-jsonl-path", default="data/exports/v14_strategy_cycle_log.jsonl")
    ap.add_argument("--min-cycles-total", type=int, default=7)
    ap.add_argument("--recent-cycles-required", type=int, default=3)
    ap.add_argument("--output-json", default="data/exports/v15_entry_readiness.json")
    ap.add_argument("--output-md", default="data/exports/v15_entry_readiness.md")
    args = ap.parse_args()

    release_checkpoint = _read_json(Path(args.release_checkpoint_json_path))
    cycle_rows = _read_jsonl(Path(args.cycle_log_jsonl_path))
    report = build_readiness(
        release_checkpoint,
        cycle_rows,
        min_cycles_total=int(args.min_cycles_total),
        recent_cycles_required=int(args.recent_cycles_required),
    )

    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_to_md(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output_json": str(out_json),
                "output_md": str(out_md),
                "entry_ready": report.get("entry_ready", False),
                "failed_checks": report.get("failed_checks", []),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

