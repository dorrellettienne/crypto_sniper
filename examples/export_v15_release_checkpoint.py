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


def build_checkpoint(
    v14_release_checkpoint: dict[str, Any],
    v15_entry_readiness: dict[str, Any],
    v15_ops_index: dict[str, Any],
    *,
    min_bundles: int = 1,
) -> dict[str, Any]:
    v14_ready = bool(v14_release_checkpoint.get("release_ready", False))
    entry_ready = bool(v15_entry_readiness.get("entry_ready", False))
    entry_metrics = dict(v15_entry_readiness.get("metrics") or {})
    cycles_total = int(entry_metrics.get("cycles_total", 0) or 0)
    cycles_failed = int(entry_metrics.get("cycles_failed", 0) or 0)
    recent_ok = bool(entry_metrics.get("recent_cycles_all_finalized_ok", False))
    bundle_count = int(v15_ops_index.get("bundle_count", 0) or 0)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, actual: Any, expected: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "actual": actual, "expected": expected})

    add("v14_release_ready", v14_ready, v14_ready, True)
    add("v15_entry_ready", entry_ready, entry_ready, True)
    add("v15_cycles_failed_zero", cycles_failed == 0, cycles_failed, 0)
    add("v15_recent_cycles_all_finalized_ok", recent_ok, recent_ok, True)
    add("v15_cycles_total_positive", cycles_total > 0, cycles_total, "> 0")
    add("v15_ops_bundle_count_min", bundle_count >= int(min_bundles), bundle_count, f">= {int(min_bundles)}")

    failed_checks = [str(c.get("name") or "") for c in checks if not bool(c.get("ok", False))]
    ready = len(failed_checks) == 0
    return {
        "ok": True,
        "report_version": "v1.5_release_checkpoint_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "release_ready": ready,
        "summary": ("release_ready" if ready else ("release_blocked:" + ",".join(failed_checks))),
        "failed_checks": failed_checks,
        "checks": checks,
        "inputs": {
            "v14_release_checkpoint_path": "data/exports/v14_release_checkpoint.json",
            "v15_entry_readiness_path": "data/exports/v15_entry_readiness.json",
            "v15_ops_index_path": "data/exports/v15_ops_bundles/index.json",
            "v14_release_ready": v14_ready,
            "v15_entry_ready": entry_ready,
            "cycles_total": cycles_total,
            "cycles_failed": cycles_failed,
            "recent_cycles_all_finalized_ok": recent_ok,
            "v15_ops_bundle_count": bundle_count,
        },
    }


def _to_md(report: dict[str, Any]) -> str:
    lines = [
        "# V1.5 Release Checkpoint",
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
        lines.append(
            f"- `{c.get('name')}` ok=`{c.get('ok')}` actual=`{json.dumps(c.get('actual'), separators=(',', ':'))}` expected=`{c.get('expected')}`"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export V1.5 release checkpoint from V1.4 release + V1.5 entry/ops artifacts.")
    ap.add_argument("--v14-release-checkpoint-json-path", default="data/exports/v14_release_checkpoint.json")
    ap.add_argument("--v15-entry-readiness-json-path", default="data/exports/v15_entry_readiness.json")
    ap.add_argument("--v15-ops-index-json-path", default="data/exports/v15_ops_bundles/index.json")
    ap.add_argument("--min-bundles", type=int, default=1)
    ap.add_argument("--output-json", default="data/exports/v15_release_checkpoint.json")
    ap.add_argument("--output-md", default="data/exports/v15_release_checkpoint.md")
    args = ap.parse_args()

    v14_release_checkpoint = _read_json(Path(args.v14_release_checkpoint_json_path))
    v15_entry_readiness = _read_json(Path(args.v15_entry_readiness_json_path))
    v15_ops_index = _read_json(Path(args.v15_ops_index_json_path))
    report = build_checkpoint(
        v14_release_checkpoint,
        v15_entry_readiness,
        v15_ops_index,
        min_bundles=int(args.min_bundles),
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
                "release_ready": report.get("release_ready", False),
                "failed_checks": report.get("failed_checks", []),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

