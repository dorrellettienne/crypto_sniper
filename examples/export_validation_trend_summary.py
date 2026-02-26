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


def _bundle_dirs(run_bundles_dir: Path) -> list[Path]:
    if not run_bundles_dir.exists():
        return []
    return sorted([p for p in run_bundles_dir.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)


def _find_daily_packet_json(bundle_dir: Path) -> Path | None:
    candidates = sorted(bundle_dir.glob("*daily_live_validation_packet*.json"))
    return candidates[0] if candidates else None


def _safe_num(v: Any) -> float | None:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except Exception:
        return None


def build_trend_summary(run_bundles_dir: Path, max_bundles: int = 50) -> dict[str, Any]:
    bundles = _bundle_dirs(run_bundles_dir)[: max(1, int(max_bundles))]
    rows: list[dict[str, Any]] = []
    for b in bundles:
        packet_path = _find_daily_packet_json(b)
        packet = _read_json(packet_path) if packet_path else None
        if not packet:
            continue
        summary = dict(packet.get("summary") or {})
        receipt = dict(packet.get("receipt") or {})
        live_summary = dict(receipt.get("live_pilot_summary") or {})
        econ = dict(live_summary.get("economics") or {})
        truth = dict(live_summary.get("settlement_truth") or {})
        rows.append(
            {
                "bundle_name": b.name,
                "signature": summary.get("signature"),
                "confirmation_status": summary.get("confirmation_status"),
                "chain_outcome_class": summary.get("chain_outcome_class"),
                "truth_confidence": summary.get("truth_confidence"),
                "fee_lamports": _safe_num(summary.get("fee_lamports")),
                "realized_slippage_bps_vs_quote": _safe_num(econ.get("realized_slippage_bps_vs_quote")),
                "quote_vs_settlement_mismatch": bool(econ.get("quote_vs_settlement_mismatch", False)),
                "quote_vs_settlement_mismatch_class": econ.get("quote_vs_settlement_mismatch_class"),
                "truth_terminal_state": truth.get("terminal_state"),
                "tx_present": bool(summary.get("tx_present")),
            }
        )

    finalized = [r for r in rows if r.get("confirmation_status") == "finalized"]
    reconciled = [r for r in rows if r.get("chain_outcome_class") == "live_confirmed_reconciled"]
    truth_complete = [r for r in rows if r.get("truth_confidence") == "truth_complete"]
    tx_present = [r for r in rows if r.get("tx_present")]
    mismatches = [r for r in rows if r.get("quote_vs_settlement_mismatch")]
    slippages = [float(r["realized_slippage_bps_vs_quote"]) for r in rows if r.get("realized_slippage_bps_vs_quote") is not None]
    fees = [float(r["fee_lamports"]) for r in rows if r.get("fee_lamports") is not None]

    mismatch_classes: dict[str, int] = {}
    confirmation_counts: dict[str, int] = {}
    chain_outcome_counts: dict[str, int] = {}
    for r in rows:
        mc = str(r.get("quote_vs_settlement_mismatch_class") or "")
        if mc:
            mismatch_classes[mc] = mismatch_classes.get(mc, 0) + 1
        cs = str(r.get("confirmation_status") or "")
        if cs:
            confirmation_counts[cs] = confirmation_counts.get(cs, 0) + 1
        co = str(r.get("chain_outcome_class") or "")
        if co:
            chain_outcome_counts[co] = chain_outcome_counts.get(co, 0) + 1

    def _avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 6) if vals else None

    out = {
        "ok": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_bundles_dir": str(run_bundles_dir),
        "bundles_scanned": len(bundles),
        "bundles_with_packets": len(rows),
        "metrics": {
            "finalized_count": len(finalized),
            "reconciled_count": len(reconciled),
            "truth_complete_count": len(truth_complete),
            "tx_present_count": len(tx_present),
            "quote_mismatch_count": len(mismatches),
            "avg_realized_slippage_bps": _avg(slippages),
            "worst_realized_slippage_bps": (round(max(slippages), 6) if slippages else None),
            "avg_fee_lamports": _avg(fees),
            "max_fee_lamports": (int(max(fees)) if fees else None),
        },
        "counts": {
            "confirmation_status": confirmation_counts,
            "chain_outcome_class": chain_outcome_counts,
            "quote_mismatch_class": mismatch_classes,
        },
        "recent_runs": rows[:10],
    }
    return out


def _to_markdown(summary: dict[str, Any]) -> str:
    m = dict(summary.get("metrics") or {})
    counts = dict(summary.get("counts") or {})
    recent = list(summary.get("recent_runs") or [])
    lines = [
        "# Validation Trend Summary",
        "",
        f"- generated_at_utc: `{summary.get('generated_at_utc')}`",
        f"- run_bundles_dir: `{summary.get('run_bundles_dir')}`",
        f"- bundles_scanned: `{summary.get('bundles_scanned')}`",
        f"- bundles_with_packets: `{summary.get('bundles_with_packets')}`",
        "",
        "## Metrics",
        "",
        f"- finalized_count: `{m.get('finalized_count')}`",
        f"- reconciled_count: `{m.get('reconciled_count')}`",
        f"- truth_complete_count: `{m.get('truth_complete_count')}`",
        f"- quote_mismatch_count: `{m.get('quote_mismatch_count')}`",
        f"- avg_realized_slippage_bps: `{m.get('avg_realized_slippage_bps')}`",
        f"- worst_realized_slippage_bps: `{m.get('worst_realized_slippage_bps')}`",
        f"- avg_fee_lamports: `{m.get('avg_fee_lamports')}`",
        f"- max_fee_lamports: `{m.get('max_fee_lamports')}`",
        "",
        "## Counts",
        "",
        f"- confirmation_status: `{json.dumps(counts.get('confirmation_status') or {}, separators=(',', ':'))}`",
        f"- chain_outcome_class: `{json.dumps(counts.get('chain_outcome_class') or {}, separators=(',', ':'))}`",
        f"- quote_mismatch_class: `{json.dumps(counts.get('quote_mismatch_class') or {}, separators=(',', ':'))}`",
        "",
        "## Recent Runs",
        "",
    ]
    if not recent:
        lines.append("_No runs found._")
    else:
        for r in recent:
            lines.append(
                f"- `{r.get('bundle_name')}` sig=`{r.get('signature')}` conf=`{r.get('confirmation_status')}` chain=`{r.get('chain_outcome_class')}` slip_bps=`{r.get('realized_slippage_bps_vs_quote')}` fee=`{r.get('fee_lamports')}`"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a trend summary over validation run bundles.")
    parser.add_argument("--run-bundles-dir", default="data/exports/run_bundles")
    parser.add_argument("--max-bundles", type=int, default=50)
    parser.add_argument("--output-json", default="data/exports/run_bundles/trend_summary.json")
    parser.add_argument("--output-md", default="data/exports/run_bundles/trend_summary.md")
    args = parser.parse_args()

    summary = build_trend_summary(Path(args.run_bundles_dir), max_bundles=args.max_bundles)
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    Path(args.output_md).write_text(_to_markdown(summary), encoding="utf-8")
    print(json.dumps({"ok": True, "output_json": args.output_json, "output_md": args.output_md, "bundles_with_packets": summary.get("bundles_with_packets", 0)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
