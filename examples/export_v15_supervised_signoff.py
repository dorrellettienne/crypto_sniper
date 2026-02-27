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


def build_signoff(
    entry_readiness: dict[str, Any],
    release_checkpoint: dict[str, Any],
    ops_index: dict[str, Any],
    *,
    min_bundles: int = 1,
) -> dict[str, Any]:
    entry_ready = bool(entry_readiness.get("entry_ready", False))
    release_ready = bool(release_checkpoint.get("release_ready", False))
    bundle_count = int(ops_index.get("bundle_count", 0) or 0)
    entry_failed = list(entry_readiness.get("failed_checks") or [])
    release_failed = list(release_checkpoint.get("failed_checks") or [])
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, actual: Any, expected: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "actual": actual, "expected": expected})

    add("v15_entry_ready", entry_ready, entry_ready, True)
    add("v15_release_ready", release_ready, release_ready, True)
    add("v15_ops_bundle_count_min", bundle_count >= int(min_bundles), bundle_count, f">= {int(min_bundles)}")

    failed_checks = [str(c.get("name") or "") for c in checks if not bool(c.get("ok", False))]
    signoff_ready = len(failed_checks) == 0
    return {
        "ok": True,
        "report_version": "v1.5_supervised_signoff_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "signoff_ready": signoff_ready,
        "summary": ("signoff_ready" if signoff_ready else ("signoff_blocked:" + ",".join(failed_checks))),
        "failed_checks": failed_checks,
        "checks": checks,
        "inputs": {
            "v15_entry_readiness_path": "data/exports/v15_entry_readiness.json",
            "v15_release_checkpoint_path": "data/exports/v15_release_checkpoint.json",
            "v15_ops_index_path": "data/exports/v15_ops_bundles/index.json",
            "entry_ready": entry_ready,
            "release_ready": release_ready,
            "entry_failed_checks": entry_failed,
            "release_failed_checks": release_failed,
            "v15_ops_bundle_count": bundle_count,
        },
    }


def _to_md(report: dict[str, Any]) -> str:
    lines = [
        "# V1.5 Supervised Signoff",
        "",
        f"- generated_at_utc: `{report.get('generated_at_utc')}`",
        f"- signoff_ready: `{report.get('signoff_ready')}`",
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
    ap = argparse.ArgumentParser(description="Export V1.5 supervised signoff from entry/release/ops artifacts.")
    ap.add_argument("--v15-entry-readiness-json-path", default="data/exports/v15_entry_readiness.json")
    ap.add_argument("--v15-release-checkpoint-json-path", default="data/exports/v15_release_checkpoint.json")
    ap.add_argument("--v15-ops-index-json-path", default="data/exports/v15_ops_bundles/index.json")
    ap.add_argument("--min-bundles", type=int, default=1)
    ap.add_argument("--output-json", default="data/exports/v15_supervised_signoff.json")
    ap.add_argument("--output-md", default="data/exports/v15_supervised_signoff.md")
    args = ap.parse_args()

    entry_readiness = _read_json(Path(args.v15_entry_readiness_json_path))
    release_checkpoint = _read_json(Path(args.v15_release_checkpoint_json_path))
    ops_index = _read_json(Path(args.v15_ops_index_json_path))
    report = build_signoff(
        entry_readiness,
        release_checkpoint,
        ops_index,
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
                "signoff_ready": report.get("signoff_ready", False),
                "failed_checks": report.get("failed_checks", []),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

