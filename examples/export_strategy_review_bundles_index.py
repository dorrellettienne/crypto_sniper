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


def build_index(strategy_bundles_dir: Path) -> dict[str, Any]:
    bundles: list[dict[str, Any]] = []
    if strategy_bundles_dir.exists():
        dirs = [p for p in strategy_bundles_dir.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for bundle_dir in dirs:
            packet_json = bundle_dir / "strategy_decision_review_packet.json"
            packet_md = bundle_dir / "strategy_decision_review_packet.md"
            packet = _read_json(packet_json) if packet_json.exists() else None
            summary = dict((packet or {}).get("summary") or {})
            trace_report = dict((packet or {}).get("strategy_decision_trace_report") or {})
            entry_base = dict(trace_report.get("entry_rule_config_base") or {})
            entry_effective = dict(trace_report.get("entry_rule_config_effective") or {})
            entry_rec = dict(trace_report.get("entry_feedback_recommendation") or {})
            score_band_rec = dict(trace_report.get("score_band_gate_recommendation") or {})
            exit_policy_rec = dict(trace_report.get("exit_policy_feedback_recommendation") or {})
            token_mem = dict(trace_report.get("token_memory_weighted_summary") or {})
            token_mem_agg = dict(token_mem.get("aggregate_weighted") or {})
            bundles.append(
                {
                    "bundle_name": bundle_dir.name,
                    "bundle_dir": str(bundle_dir),
                    "strategy_decision_review_packet_json": _snap(packet_json),
                    "strategy_decision_review_packet_md": _snap(packet_md),
                    "summary": {
                        "guard_candidates_promoted": summary.get("guard_candidates_promoted"),
                        "entry_candidates_enter": summary.get("entry_candidates_enter"),
                        "exit_policy_valid": summary.get("exit_policy_valid"),
                        "promoted_token_addresses": summary.get("guard_promoted_token_addresses"),
                        "strategy_trend_traces_total": summary.get("strategy_trend_traces_total"),
                    },
                    "adaptive_override_audit": {
                        "entry_adaptive_enabled": bool(summary.get("entry_adaptive_enabled", False)),
                        "entry_min_score_total_base": entry_base.get("min_score_total"),
                        "entry_min_score_total_effective": entry_effective.get("min_score_total"),
                        "entry_require_probe_ok_base": entry_base.get("require_probe_ok"),
                        "entry_require_probe_ok_effective": entry_effective.get("require_probe_ok"),
                        "entry_recommendation_reasons": list(entry_rec.get("recommendation_reasons") or []),
                        "score_band_recommended_min_score_total": score_band_rec.get("recommended_min_score_total"),
                        "score_band_recommendation_reasons": list(score_band_rec.get("recommendation_reasons") or []),
                        "exit_policy_adaptive_enabled": bool(trace_report.get("exit_policy_adaptive_enabled", False)),
                        "exit_policy_selected_from_feedback": bool(trace_report.get("exit_policy_selected_from_feedback", False)),
                        "exit_policy_recommended_fingerprint": exit_policy_rec.get("recommended_exit_policy_fingerprint"),
                        "exit_policy_recommended_label": exit_policy_rec.get("recommended_exit_policy_label"),
                        "exit_policy_recommendation_reasons": list(exit_policy_rec.get("recommendation_reasons") or []),
                        "token_memory_promoted_reconciled_rate_weighted": token_mem_agg.get("promoted_reconciled_rate_weighted"),
                        "token_memory_promoted_quote_mismatch_rate_weighted": token_mem_agg.get("promoted_quote_mismatch_rate_weighted"),
                    },
                    "generated_at_utc": (packet or {}).get("generated_at_utc"),
                }
            )
    return {
        "ok": True,
        "report_version": "v1.3_strategy_review_bundles_index_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_bundles_dir": str(strategy_bundles_dir),
        "bundle_count": len(bundles),
        "bundles": bundles,
    }


def _md(idx: dict[str, Any]) -> str:
    lines = [
        "# Strategy Review Bundles Index",
        "",
        f"- generated_at_utc: `{idx.get('generated_at_utc')}`",
        f"- strategy_bundles_dir: `{idx.get('strategy_bundles_dir')}`",
        f"- bundle_count: `{idx.get('bundle_count')}`",
        "",
        "## Bundles",
        "",
    ]
    bundles = list(idx.get("bundles") or [])
    if not bundles:
        lines.append("_No strategy bundles found._")
        return "\n".join(lines) + "\n"
    for b in bundles:
        s = dict(b.get("summary") or {})
        lines.extend(
            [
                f"### `{b.get('bundle_name')}`",
                f"- guard_candidates_promoted: `{s.get('guard_candidates_promoted')}`",
                f"- entry_candidates_enter: `{s.get('entry_candidates_enter')}`",
                f"- exit_policy_valid: `{s.get('exit_policy_valid')}`",
                f"- promoted_token_addresses: `{json.dumps(s.get('promoted_token_addresses') or [], separators=(',', ':'))}`",
                f"- adaptive_override_audit: `{json.dumps((b.get('adaptive_override_audit') or {}), separators=(',', ':'))}`",
                f"- packet_json: `{((b.get('strategy_decision_review_packet_json') or {}).get('path'))}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Export an index of strategy review bundles.")
    ap.add_argument("--strategy-bundles-dir", default="data/exports/strategy_bundles")
    ap.add_argument("--output-json", default="data/exports/strategy_bundles/index.json")
    ap.add_argument("--output-md", default="data/exports/strategy_bundles/index.md")
    args = ap.parse_args()

    idx = build_index(Path(args.strategy_bundles_dir))
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
