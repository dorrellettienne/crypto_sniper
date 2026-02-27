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


def _safe_float(v: Any) -> float | None:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except Exception:
        return None


def build_summary(v16_bundles_dir: Path, max_bundles: int = 50) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if v16_bundles_dir.exists():
        dirs = [p for p in v16_bundles_dir.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for b in dirs[: max(1, int(max_bundles))]:
            receipt_path = b / "v16_latest_live_receipt.json"
            receipt = _read_json(receipt_path)
            if not receipt:
                continue
            live_summary = dict(receipt.get("live_pilot_summary") or {})
            econ = dict(live_summary.get("economics") or {})
            rows.append(
                {
                    "bundle_name": b.name,
                    "signature": receipt.get("signature"),
                    "confirmation_status": ((receipt.get("rpc_status") or {}).get("confirmation_status")),
                    "chain_outcome_class": live_summary.get("chain_outcome_class"),
                    "tx_present": bool(receipt.get("tx_present")),
                    "realized_slippage_bps_vs_quote": _safe_float(econ.get("realized_slippage_bps_vs_quote")),
                    "quote_vs_settlement_mismatch_effective": bool(econ.get("quote_vs_settlement_mismatch_effective", econ.get("quote_vs_settlement_mismatch", False))),
                    "estimated_notional_usd": _safe_float(econ.get("estimated_notional_usd")),
                    "estimated_network_fee_usd": _safe_float(econ.get("estimated_network_fee_usd")),
                }
            )

    finalized = [r for r in rows if r.get("confirmation_status") == "finalized"]
    submitted = [r for r in rows if str(r.get("signature") or "")]
    mismatches = [r for r in rows if bool(r.get("quote_vs_settlement_mismatch_effective"))]
    slippages = [float(r["realized_slippage_bps_vs_quote"]) for r in rows if r.get("realized_slippage_bps_vs_quote") is not None]
    fee_to_notional: list[float] = []
    for r in rows:
        n = _safe_float(r.get("estimated_notional_usd"))
        f = _safe_float(r.get("estimated_network_fee_usd"))
        if n and n > 0 and f is not None:
            fee_to_notional.append(float(f) / float(n))

    def _avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 6) if vals else None

    metrics = {
        "runs_total": len(rows),
        "submitted_count": len(submitted),
        "finalized_count": len(finalized),
        "finalized_rate": (round(len(finalized) / len(submitted), 6) if submitted else None),
        "quote_mismatch_count": len(mismatches),
        "quote_mismatch_rate": (round(len(mismatches) / len(rows), 6) if rows else None),
        "avg_realized_slippage_bps": _avg(slippages),
        "worst_realized_slippage_bps": (round(max(slippages), 6) if slippages else None),
        "avg_estimated_fee_to_notional_ratio": _avg(fee_to_notional),
    }

    return {
        "ok": True,
        "report_version": "v1.6_performance_summary_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "v16_bundles_dir": str(v16_bundles_dir),
        "metrics": metrics,
        "recent_runs": rows[:10],
    }


def _to_md(summary: dict[str, Any]) -> str:
    m = dict(summary.get("metrics") or {})
    recent = list(summary.get("recent_runs") or [])
    lines = [
        "# V1.6 Performance Summary",
        "",
        f"- generated_at_utc: `{summary.get('generated_at_utc')}`",
        f"- v16_bundles_dir: `{summary.get('v16_bundles_dir')}`",
        "",
        "## Metrics",
        "",
        f"- runs_total: `{m.get('runs_total')}`",
        f"- submitted_count: `{m.get('submitted_count')}`",
        f"- finalized_count: `{m.get('finalized_count')}`",
        f"- finalized_rate: `{m.get('finalized_rate')}`",
        f"- quote_mismatch_count: `{m.get('quote_mismatch_count')}`",
        f"- quote_mismatch_rate: `{m.get('quote_mismatch_rate')}`",
        f"- avg_realized_slippage_bps: `{m.get('avg_realized_slippage_bps')}`",
        f"- worst_realized_slippage_bps: `{m.get('worst_realized_slippage_bps')}`",
        f"- avg_estimated_fee_to_notional_ratio: `{m.get('avg_estimated_fee_to_notional_ratio')}`",
        "",
        "## Recent Runs",
        "",
    ]
    if not recent:
        lines.append("_No V1.6 runs found._")
    else:
        for r in recent:
            lines.append(
                f"- `{r.get('bundle_name')}` sig=`{r.get('signature')}` conf=`{r.get('confirmation_status')}` slip=`{r.get('realized_slippage_bps_vs_quote')}` mismatch=`{r.get('quote_vs_settlement_mismatch_effective')}`"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export V1.6 supervised discovery performance summary.")
    ap.add_argument("--v16-bundles-dir", default="data/exports/v16_bundles")
    ap.add_argument("--max-bundles", type=int, default=50)
    ap.add_argument("--output-json", default="data/exports/v16_performance_summary.json")
    ap.add_argument("--output-md", default="data/exports/v16_performance_summary.md")
    args = ap.parse_args()

    summary = build_summary(Path(args.v16_bundles_dir), max_bundles=int(args.max_bundles))
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    out_md.write_text(_to_md(summary), encoding="utf-8")
    print(json.dumps({"ok": True, "output_json": str(out_json), "output_md": str(out_md), "runs_total": (summary.get("metrics") or {}).get("runs_total", 0)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

