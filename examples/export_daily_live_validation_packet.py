import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_module_from_path(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable_to_load_module:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _latest_by_glob(base: Path, pattern: str) -> Path | None:
    files = sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _artifact_snapshot(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"present": False, "path": ""}
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


def build_daily_packet(exports_dir: Path, owner_pubkey: str | None = None) -> dict[str, Any]:
    examples_dir = Path(__file__).resolve().parent
    receipt_mod = _load_module_from_path("export_latest_live_submit_receipt_mod", examples_dir / "export_latest_live_submit_receipt.py")
    status_mod = _load_module_from_path("check_latest_live_submit_signature_status_mod", examples_dir / "check_latest_live_submit_signature_status.py")

    receipt = receipt_mod.build_receipt(exports_dir, owner_pubkey=owner_pubkey)  # type: ignore[attr-defined]
    try:
        # Reuse helper main logic by calling its internals directly is not exposed; emulate with receipt-derived status.
        latest_status = {
            "ok": bool(receipt.get("ok")),
            "signature": receipt.get("signature"),
            "rpc_status": dict(receipt.get("rpc_status") or {}),
            "tx_present": receipt.get("tx_present"),
            "status_error": receipt.get("status_error"),
            "tx_error": receipt.get("tx_error"),
        }
    except Exception as exc:
        latest_status = {"ok": False, "reason": f"status_summary_failed:{exc}"}

    artifacts = {
        "latest_auto_window_log": _artifact_snapshot(_latest_by_glob(exports_dir, "live_pilot_service_auto_window_*.jsonl")),
        "latest_receipt_json": _artifact_snapshot(_latest_by_glob(exports_dir, "latest_live_receipt.json")),
        "latest_receipt_md": _artifact_snapshot(_latest_by_glob(exports_dir, "latest_live_receipt.md")),
        "latest_guard_report": _artifact_snapshot(_latest_by_glob(exports_dir, "pilot_live_launch_guard*.json")),
        "latest_status_helper_log_source": {"present": bool(receipt.get("ok")), "path": str(receipt.get("log") or "")},
    }

    packet = {
        "ok": bool(receipt.get("ok")),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "exports_dir": str(exports_dir),
        "owner_pubkey_filter": owner_pubkey or "",
        "receipt": receipt,
        "latest_status_summary": latest_status,
        "artifacts": artifacts,
        "summary": {
            "signature": receipt.get("signature"),
            "solscan_url": receipt.get("solscan_url"),
            "confirmation_status": ((receipt.get("rpc_status") or {}).get("confirmation_status") if isinstance(receipt.get("rpc_status"), dict) else None),
            "tx_present": receipt.get("tx_present"),
            "fee_lamports": receipt.get("fee_lamports"),
            "chain_outcome_class": ((receipt.get("live_pilot_summary") or {}).get("chain_outcome_class") if isinstance(receipt.get("live_pilot_summary"), dict) else None),
            "truth_confidence": (((receipt.get("live_pilot_summary") or {}).get("settlement_truth") or {}).get("confidence") if isinstance((receipt.get("live_pilot_summary") or {}), dict) else None),
        },
    }
    return packet


def _to_markdown(packet: dict[str, Any]) -> str:
    summary = dict(packet.get("summary") or {})
    receipt = dict(packet.get("receipt") or {})
    econ = dict((receipt.get("live_pilot_summary") or {}).get("economics") or {}) if isinstance(receipt.get("live_pilot_summary"), dict) else {}
    truth = dict((receipt.get("live_pilot_summary") or {}).get("settlement_truth") or {}) if isinstance(receipt.get("live_pilot_summary"), dict) else {}
    artifacts = dict(packet.get("artifacts") or {})

    lines = [
        "# Daily Live Validation Packet",
        "",
        f"- generated_at_utc: `{packet.get('generated_at_utc')}`",
        f"- signature: `{summary.get('signature')}`",
        f"- solscan: {summary.get('solscan_url')}",
        f"- confirmation_status: `{summary.get('confirmation_status')}`",
        f"- chain_outcome_class: `{summary.get('chain_outcome_class')}`",
        f"- truth_confidence: `{summary.get('truth_confidence')}`",
        f"- tx_present: `{summary.get('tx_present')}`",
        f"- fee_lamports: `{summary.get('fee_lamports')}`",
    ]
    if econ:
        lines.extend(
            [
                f"- realized_slippage_bps_vs_quote: `{econ.get('realized_slippage_bps_vs_quote')}`",
                f"- quote_vs_settlement_mismatch: `{econ.get('quote_vs_settlement_mismatch')}`",
                f"- quote_vs_settlement_mismatch_class: `{econ.get('quote_vs_settlement_mismatch_class')}`",
            ]
        )
    if truth:
        lines.extend(
            [
                f"- settlement_terminal_state: `{truth.get('terminal_state')}`",
                f"- owner_token_delta_raw: `{truth.get('owner_token_delta_raw')}`",
            ]
        )
    lines.append("")
    lines.append("## Artifact References")
    lines.append("")
    for name, info in artifacts.items():
        if isinstance(info, dict):
            lines.append(f"- {name}: `{info.get('path')}` present={info.get('present')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a daily live validation packet (receipt + status + artifact refs).")
    parser.add_argument("--exports-dir", default="data/exports")
    parser.add_argument("--owner-pubkey", default="")
    parser.add_argument("--output-json", default="data/exports/daily_live_validation_packet.json")
    parser.add_argument("--output-md", default="data/exports/daily_live_validation_packet.md")
    args = parser.parse_args()

    packet = build_daily_packet(Path(args.exports_dir), owner_pubkey=(args.owner_pubkey or "").strip() or None)
    Path(args.output_json).write_text(json.dumps(packet, indent=2, default=str), encoding="utf-8")
    Path(args.output_md).write_text(_to_markdown(packet), encoding="utf-8")
    print(json.dumps({"ok": bool(packet.get("ok")), "output_json": args.output_json, "output_md": args.output_md}, separators=(",", ":")))
    return 0 if packet.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
