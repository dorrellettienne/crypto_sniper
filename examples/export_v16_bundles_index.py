import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


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


def build_index(v16_bundles_dir: Path) -> dict[str, Any]:
    bundles: list[dict[str, Any]] = []
    if v16_bundles_dir.exists():
        dirs = [p for p in v16_bundles_dir.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for b in dirs:
            receipt_path = b / "v16_latest_live_receipt.json"
            receipt = _read_json(receipt_path) if receipt_path.exists() else None
            live_summary = dict((receipt or {}).get("live_pilot_summary") or {})
            econ = dict(live_summary.get("economics") or {})
            bundles.append(
                {
                    "bundle_name": b.name,
                    "bundle_dir": str(b),
                    "v16_latest_live_receipt_json": _snap(receipt_path),
                    "v16_scored_discovery_report_json": _snap(b / "v16_scored_discovery_report.json"),
                    "v16_selected_promoted_candidate_json": _snap(b / "v16_selected_promoted_candidate.json"),
                    "summary": {
                        "signature": (receipt or {}).get("signature"),
                        "confirmation_status": ((receipt or {}).get("rpc_status") or {}).get("confirmation_status"),
                        "chain_outcome_class": live_summary.get("chain_outcome_class"),
                        "tx_present": bool((receipt or {}).get("tx_present")),
                        "realized_slippage_bps_vs_quote": econ.get("realized_slippage_bps_vs_quote"),
                        "quote_vs_settlement_mismatch": econ.get("quote_vs_settlement_mismatch"),
                    },
                    "generated_at_utc": (receipt or {}).get("generated_at_utc"),
                }
            )

    return {
        "ok": True,
        "report_version": "v1.6_bundles_index_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "v16_bundles_dir": str(v16_bundles_dir),
        "bundle_count": len(bundles),
        "bundles": bundles,
    }


def _to_md(idx: dict[str, Any]) -> str:
    lines = [
        "# V1.6 Bundles Index",
        "",
        f"- generated_at_utc: `{idx.get('generated_at_utc')}`",
        f"- v16_bundles_dir: `{idx.get('v16_bundles_dir')}`",
        f"- bundle_count: `{idx.get('bundle_count')}`",
        "",
        "## Bundles",
        "",
    ]
    bundles = list(idx.get("bundles") or [])
    if not bundles:
        lines.append("_No bundles found._")
        return "\n".join(lines) + "\n"
    for b in bundles:
        s = dict(b.get("summary") or {})
        lines.extend(
            [
                f"### `{b.get('bundle_name')}`",
                f"- signature: `{s.get('signature')}`",
                f"- confirmation_status: `{s.get('confirmation_status')}`",
                f"- chain_outcome_class: `{s.get('chain_outcome_class')}`",
                f"- tx_present: `{s.get('tx_present')}`",
                f"- realized_slippage_bps_vs_quote: `{s.get('realized_slippage_bps_vs_quote')}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export index for V1.6 supervised discovery bundles.")
    ap.add_argument("--v16-bundles-dir", default="data/exports/v16_bundles")
    ap.add_argument("--output-json", default="data/exports/v16_bundles/index.json")
    ap.add_argument("--output-md", default="data/exports/v16_bundles/index.md")
    args = ap.parse_args()

    idx = build_index(Path(args.v16_bundles_dir))
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(idx, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_to_md(idx), encoding="utf-8")
    print(
        json.dumps(
            {"ok": True, "output_json": str(out_json), "output_md": str(out_md), "bundle_count": idx.get("bundle_count", 0)},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

