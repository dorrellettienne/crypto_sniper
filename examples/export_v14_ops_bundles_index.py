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


def build_index(v14_ops_bundles_dir: Path) -> dict[str, Any]:
    bundles: list[dict[str, Any]] = []
    if v14_ops_bundles_dir.exists():
        dirs = [p for p in v14_ops_bundles_dir.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for bundle_dir in dirs:
            signoff_json = bundle_dir / "v14_strategy_cycle_signoff.json"
            signoff = _read_json(signoff_json) if signoff_json.exists() else None
            bundles.append(
                {
                    "bundle_name": bundle_dir.name,
                    "bundle_dir": str(bundle_dir),
                    "v14_strategy_cycle_signoff_json": _snap(signoff_json),
                    "v14_strategy_cycle_signoff_md": _snap(bundle_dir / "v14_strategy_cycle_signoff.md"),
                    "v14_strategy_cycle_log_jsonl": _snap(bundle_dir / "v14_strategy_cycle_log.jsonl"),
                    "strategy_decision_review_packet_json": _snap(bundle_dir / "strategy_decision_review_packet.json"),
                    "strategy_decision_review_packet_md": _snap(bundle_dir / "strategy_decision_review_packet.md"),
                    "summary": {
                        "cycles_total": ((signoff or {}).get("cycles_total")),
                        "cycles_finalized_ok": ((signoff or {}).get("cycles_finalized_ok")),
                        "cycles_failed": ((signoff or {}).get("cycles_failed")),
                        "signoff_ready": ((signoff or {}).get("signoff_ready")),
                    },
                    "generated_at_utc": ((signoff or {}).get("generated_at_utc")),
                }
            )
    return {
        "ok": True,
        "report_version": "v1.4_ops_bundles_index_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "v14_ops_bundles_dir": str(v14_ops_bundles_dir),
        "bundle_count": len(bundles),
        "bundles": bundles,
    }


def _md(idx: dict[str, Any]) -> str:
    lines = [
        "# V1.4 Ops Bundles Index",
        "",
        f"- generated_at_utc: `{idx.get('generated_at_utc')}`",
        f"- v14_ops_bundles_dir: `{idx.get('v14_ops_bundles_dir')}`",
        f"- bundle_count: `{idx.get('bundle_count')}`",
        "",
        "## Bundles",
        "",
    ]
    bundles = list(idx.get("bundles") or [])
    if not bundles:
        lines.append("_No V1.4 ops bundles found._")
        return "\n".join(lines) + "\n"
    for b in bundles:
        s = dict(b.get("summary") or {})
        lines.extend(
            [
                f"### `{b.get('bundle_name')}`",
                f"- cycles_total: `{s.get('cycles_total')}`",
                f"- cycles_finalized_ok: `{s.get('cycles_finalized_ok')}`",
                f"- cycles_failed: `{s.get('cycles_failed')}`",
                f"- signoff_ready: `{s.get('signoff_ready')}`",
                f"- signoff_json: `{((b.get('v14_strategy_cycle_signoff_json') or {}).get('path'))}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export index for V1.4 ops bundles.")
    ap.add_argument("--v14-ops-bundles-dir", default="data/exports/v14_ops_bundles")
    ap.add_argument("--output-json", default="data/exports/v14_ops_bundles/index.json")
    ap.add_argument("--output-md", default="data/exports/v14_ops_bundles/index.md")
    args = ap.parse_args()

    idx = build_index(Path(args.v14_ops_bundles_dir))
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(idx, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_md(idx), encoding="utf-8")
    print(json.dumps({"ok": True, "output_json": str(out_json), "output_md": str(out_md), "bundle_count": idx.get("bundle_count", 0)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

