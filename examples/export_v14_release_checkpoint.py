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


def build_checkpoint(signoff: dict[str, Any], ops_index: dict[str, Any], *, min_bundles: int = 1) -> dict[str, Any]:
    cycles_total = int(signoff.get("cycles_total", 0) or 0)
    cycles_finalized_ok = int(signoff.get("cycles_finalized_ok", 0) or 0)
    cycles_failed = int(signoff.get("cycles_failed", 0) or 0)
    signoff_ready = bool(signoff.get("signoff_ready", False))
    bundle_count = int(ops_index.get("bundle_count", 0) or 0)
    checks = []

    def add(name: str, ok: bool, actual: Any, expected: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "actual": actual, "expected": expected})

    add("signoff_ready", signoff_ready, signoff_ready, True)
    add("cycles_finalized_match_total", cycles_total > 0 and cycles_finalized_ok == cycles_total, {"cycles_total": cycles_total, "cycles_finalized_ok": cycles_finalized_ok}, "all_cycles_finalized")
    add("cycles_failed_zero", cycles_failed == 0, cycles_failed, 0)
    add("ops_bundle_count_min", bundle_count >= int(min_bundles), bundle_count, f">= {int(min_bundles)}")

    failed_checks = [str(c.get("name") or "") for c in checks if not bool(c.get("ok", False))]
    ready = len(failed_checks) == 0
    return {
        "ok": True,
        "report_version": "v1.4_release_checkpoint_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "release_ready": ready,
        "summary": ("release_ready" if ready else ("release_blocked:" + ",".join(failed_checks))),
        "failed_checks": failed_checks,
        "checks": checks,
        "inputs": {
            "signoff_report_path": "data/exports/v14_strategy_cycle_signoff.json",
            "ops_bundles_index_path": "data/exports/v14_ops_bundles/index.json",
            "cycles_total": cycles_total,
            "cycles_finalized_ok": cycles_finalized_ok,
            "cycles_failed": cycles_failed,
            "signoff_ready": signoff_ready,
            "ops_bundle_count": bundle_count,
        },
    }


def _to_md(report: dict[str, Any]) -> str:
    lines = [
        "# V1.4 Release Checkpoint",
        "",
        f"- generated_at_utc: `{report.get('generated_at_utc')}`",
        f"- release_ready: `{report.get('release_ready')}`",
        f"- summary: `{report.get('summary')}`",
        f"- failed_checks: `{json.dumps(report.get('failed_checks') or [], separators=(',', ':'))}`",
        "",
        "## Checks",
        "",
    ]
    for c in list(report.get("checks") or []):
        lines.append(f"- `{c.get('name')}` ok=`{c.get('ok')}` actual=`{json.dumps(c.get('actual'), separators=(',', ':'))}` expected=`{c.get('expected')}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export V1.4 release checkpoint from signoff + ops bundle index.")
    ap.add_argument("--signoff-json-path", default="data/exports/v14_strategy_cycle_signoff.json")
    ap.add_argument("--ops-index-json-path", default="data/exports/v14_ops_bundles/index.json")
    ap.add_argument("--min-bundles", type=int, default=1)
    ap.add_argument("--output-json", default="data/exports/v14_release_checkpoint.json")
    ap.add_argument("--output-md", default="data/exports/v14_release_checkpoint.md")
    args = ap.parse_args()

    signoff = _read_json(Path(args.signoff_json_path))
    ops_index = _read_json(Path(args.ops_index_json_path))
    report = build_checkpoint(signoff, ops_index, min_bundles=int(args.min_bundles))

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
                "release_ready": report.get("release_ready", False),
                "failed_checks": report.get("failed_checks", []),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

