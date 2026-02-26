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
    return {
        "present": True,
        "path": str(path),
        "size_bytes": int(st.st_size),
        "mtime_unix_ms": int(st.st_mtime * 1000),
    }


def build_bundles_index(run_bundles_dir: Path) -> dict[str, Any]:
    bundles = []
    if run_bundles_dir.exists():
        for bundle_dir in sorted([p for p in run_bundles_dir.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True):
            idx_json = bundle_dir / "run_artifact_index.json"
            idx_md = bundle_dir / "run_artifact_index.md"
            idx = _read_json(idx_json) if idx_json.exists() else None
            summary = dict((idx or {}).get("summary") or {})
            bundles.append(
                {
                    "bundle_dir": str(bundle_dir),
                    "bundle_name": bundle_dir.name,
                    "run_artifact_index_json": _snap(idx_json) if idx_json.exists() else {"present": False, "path": str(idx_json)},
                    "run_artifact_index_md": _snap(idx_md) if idx_md.exists() else {"present": False, "path": str(idx_md)},
                    "summary": {
                        "signature": summary.get("signature"),
                        "confirmation_status": summary.get("confirmation_status"),
                        "chain_outcome_class": summary.get("chain_outcome_class"),
                        "truth_confidence": summary.get("truth_confidence"),
                        "fee_lamports": summary.get("fee_lamports"),
                    },
                    "artifacts": dict((idx or {}).get("artifacts") or {}),
                    "generated_at_utc": (idx or {}).get("generated_at_utc"),
                }
            )
    return {
        "ok": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_bundles_dir": str(run_bundles_dir),
        "bundle_count": len(bundles),
        "bundles": bundles,
    }


def _to_markdown(index: dict[str, Any]) -> str:
    bundles = list(index.get("bundles") or [])
    lines = [
        "# Run Bundles Index",
        "",
        f"- generated_at_utc: `{index.get('generated_at_utc')}`",
        f"- run_bundles_dir: `{index.get('run_bundles_dir')}`",
        f"- bundle_count: `{index.get('bundle_count')}`",
        "",
    ]
    if not bundles:
        lines.append("_No bundles found._")
        return "\n".join(lines) + "\n"
    lines.extend(["## Bundles", ""])
    for b in bundles:
        s = dict(b.get("summary") or {})
        lines.extend(
            [
                f"### `{b.get('bundle_name')}`",
                f"- signature: `{s.get('signature')}`",
                f"- confirmation_status: `{s.get('confirmation_status')}`",
                f"- chain_outcome_class: `{s.get('chain_outcome_class')}`",
                f"- truth_confidence: `{s.get('truth_confidence')}`",
                f"- fee_lamports: `{s.get('fee_lamports')}`",
                f"- index_json: `{((b.get('run_artifact_index_json') or {}).get('path'))}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export an index of run_bundles validation artifacts.")
    parser.add_argument("--run-bundles-dir", default="data/exports/run_bundles")
    parser.add_argument("--output-json", default="data/exports/run_bundles/index.json")
    parser.add_argument("--output-md", default="data/exports/run_bundles/index.md")
    args = parser.parse_args()

    index = build_bundles_index(Path(args.run_bundles_dir))
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
    Path(args.output_md).write_text(_to_markdown(index), encoding="utf-8")
    print(json.dumps({"ok": True, "output_json": args.output_json, "output_md": args.output_md, "bundle_count": index.get("bundle_count", 0)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
