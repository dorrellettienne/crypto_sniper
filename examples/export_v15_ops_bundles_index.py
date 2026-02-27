import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _snap(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
    except Exception:
        return {"present": False, "path": str(path)}
    return {"present": True, "path": str(path), "size_bytes": int(st.st_size), "mtime_unix_ms": int(st.st_mtime * 1000)}


def build_index(v15_ops_bundles_dir: Path) -> dict[str, Any]:
    bundles: list[dict[str, Any]] = []
    if v15_ops_bundles_dir.exists():
        dirs = [p for p in v15_ops_bundles_dir.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for bundle_dir in dirs:
            release_json = bundle_dir / "v15_release_checkpoint.json"
            release = _read_json(release_json) if release_json.exists() else None
            entry_json = bundle_dir / "v15_entry_readiness.json"
            entry = _read_json(entry_json) if entry_json.exists() else None
            bundles.append(
                {
                    "bundle_name": bundle_dir.name,
                    "bundle_dir": str(bundle_dir),
                    "v15_release_checkpoint_json": _snap(release_json),
                    "v15_release_checkpoint_md": _snap(bundle_dir / "v15_release_checkpoint.md"),
                    "v15_entry_readiness_json": _snap(entry_json),
                    "v15_entry_readiness_md": _snap(bundle_dir / "v15_entry_readiness.md"),
                    "v14_strategy_cycle_log_jsonl": _snap(bundle_dir / "v14_strategy_cycle_log.jsonl"),
                    "summary": {
                        "release_ready": ((release or {}).get("release_ready")),
                        "entry_ready": ((entry or {}).get("entry_ready")),
                        "cycles_total": (((entry or {}).get("metrics") or {}).get("cycles_total")),
                        "cycles_failed": (((entry or {}).get("metrics") or {}).get("cycles_failed")),
                    },
                    "generated_at_utc": ((release or {}).get("generated_at_utc")) or ((entry or {}).get("generated_at_utc")),
                }
            )
    return {
        "ok": True,
        "report_version": "v1.5_ops_bundles_index_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "v15_ops_bundles_dir": str(v15_ops_bundles_dir),
        "bundle_count": len(bundles),
        "bundles": bundles,
    }


def _md(idx: dict[str, Any]) -> str:
    lines = [
        "# V1.5 Ops Bundles Index",
        "",
        f"- generated_at_utc: `{idx.get('generated_at_utc')}`",
        f"- v15_ops_bundles_dir: `{idx.get('v15_ops_bundles_dir')}`",
        f"- bundle_count: `{idx.get('bundle_count')}`",
        "",
        "## Bundles",
        "",
    ]
    bundles = list(idx.get("bundles") or [])
    if not bundles:
        lines.append("_No V1.5 ops bundles found._")
        return "\n".join(lines) + "\n"
    for b in bundles:
        s = dict(b.get("summary") or {})
        lines.extend(
            [
                f"### `{b.get('bundle_name')}`",
                f"- release_ready: `{s.get('release_ready')}`",
                f"- entry_ready: `{s.get('entry_ready')}`",
                f"- cycles_total: `{s.get('cycles_total')}`",
                f"- cycles_failed: `{s.get('cycles_failed')}`",
                f"- v15_release_checkpoint_json: `{((b.get('v15_release_checkpoint_json') or {}).get('path'))}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export index for V1.5 ops bundles.")
    ap.add_argument("--v15-ops-bundles-dir", default="data/exports/v15_ops_bundles")
    ap.add_argument("--output-json", default="data/exports/v15_ops_bundles/index.json")
    ap.add_argument("--output-md", default="data/exports/v15_ops_bundles/index.md")
    args = ap.parse_args()

    idx = build_index(Path(args.v15_ops_bundles_dir))
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(idx, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_md(idx), encoding="utf-8")
    print(
        json.dumps(
            {"ok": True, "output_json": str(out_json), "output_md": str(out_md), "bundle_count": idx.get("bundle_count", 0)},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

