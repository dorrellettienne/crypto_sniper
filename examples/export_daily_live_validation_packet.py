import argparse
import importlib.util
import shutil
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


def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _copy_if_exists(src: Path | None, dst_dir: Path) -> dict[str, Any]:
    snap = _artifact_snapshot(src)
    if not snap.get("present"):
        return {"copied": False, "source": snap}
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name  # type: ignore[union-attr]
    shutil.copy2(src, dst)
    return {"copied": True, "source": snap, "dest": _artifact_snapshot(dst)}


def build_daily_packet(
    exports_dir: Path,
    owner_pubkey: str | None = None,
    prefer_cached_receipt: bool = True,
    rpc_retry_attempts: int = 4,
    rpc_retry_backoff_seconds: float = 1.0,
) -> dict[str, Any]:
    examples_dir = Path(__file__).resolve().parent
    receipt_mod = _load_module_from_path("export_latest_live_submit_receipt_mod", examples_dir / "export_latest_live_submit_receipt.py")
    _ = _load_module_from_path("check_latest_live_submit_signature_status_mod", examples_dir / "check_latest_live_submit_signature_status.py")

    receipt: dict[str, Any] | None = None
    latest_receipt_path = exports_dir / "latest_live_receipt.json"
    if prefer_cached_receipt:
        cached = _load_json_if_exists(latest_receipt_path)
        if isinstance(cached, dict) and cached.get("ok"):
            receipt = cached
    if receipt is None:
        receipt = receipt_mod.build_receipt(  # type: ignore[attr-defined]
            exports_dir,
            owner_pubkey=owner_pubkey,
            rpc_retry_attempts=rpc_retry_attempts,
            rpc_retry_backoff_seconds=rpc_retry_backoff_seconds,
        )
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


def build_run_artifact_index(exports_dir: Path) -> dict[str, Any]:
    patterns = {
        "auto_window_log": "live_pilot_service_auto_window_*.jsonl",
        "receipt_json": "latest_live_receipt.json",
        "receipt_md": "latest_live_receipt.md",
        "daily_packet_json": "daily_live_validation_packet.json",
        "daily_packet_md": "daily_live_validation_packet.md",
        "guard_report_json": "pilot_live_launch_guard*.json",
    }
    out: dict[str, Any] = {}
    for key, pattern in patterns.items():
        out[key] = _artifact_snapshot(_latest_by_glob(exports_dir, pattern))
    return out


def autopack_run_bundle(packet: dict[str, Any], exports_dir: Path, pack_dir: Path) -> dict[str, Any]:
    signature = str(((packet.get("summary") or {}).get("signature") or "no_sig"))
    safe_sig = signature[:12] if signature else "no_sig"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = pack_dir / f"live_validation_{stamp}_{safe_sig}"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    copies = {}
    copies["auto_window_log"] = _copy_if_exists(_latest_by_glob(exports_dir, "live_pilot_service_auto_window_*.jsonl"), bundle_dir)
    copies["receipt_json"] = _copy_if_exists(_latest_by_glob(exports_dir, "latest_live_receipt.json"), bundle_dir)
    copies["receipt_md"] = _copy_if_exists(_latest_by_glob(exports_dir, "latest_live_receipt.md"), bundle_dir)
    copies["daily_packet_json"] = _copy_if_exists(_latest_by_glob(exports_dir, "daily_live_validation_packet.json"), bundle_dir)
    copies["daily_packet_md"] = _copy_if_exists(_latest_by_glob(exports_dir, "daily_live_validation_packet.md"), bundle_dir)
    copies["guard_report_json"] = _copy_if_exists(_latest_by_glob(exports_dir, "pilot_live_launch_guard*.json"), bundle_dir)

    run_index = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "bundle_dir": str(bundle_dir),
        "summary": dict(packet.get("summary") or {}),
        "artifacts": copies,
    }
    (bundle_dir / "run_artifact_index.json").write_text(json.dumps(run_index, indent=2, default=str), encoding="utf-8")
    (bundle_dir / "run_artifact_index.md").write_text(
        "\n".join(
            [
                "# Run Artifact Index",
                "",
                f"- bundle_dir: `{bundle_dir}`",
                f"- signature: `{run_index['summary'].get('signature')}`",
                f"- confirmation_status: `{run_index['summary'].get('confirmation_status')}`",
                "",
                "## Files",
                "",
            ]
            + [
                f"- {name}: `{((info.get('dest') or {}).get('path') if isinstance(info, dict) else '')}` copied={bool((info or {}).get('copied', False))}"
                for name, info in copies.items()
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {"bundle_dir": str(bundle_dir), "run_index_json": str(bundle_dir / "run_artifact_index.json"), "run_index_md": str(bundle_dir / "run_artifact_index.md")}


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
    parser.add_argument("--pack-dir", default="", help="Optional directory to copy latest run artifacts into a dated bundle.")
    parser.add_argument("--no-reuse-cached-receipt", action="store_true")
    parser.add_argument("--rpc-retry-attempts", type=int, default=4)
    parser.add_argument("--rpc-retry-backoff-seconds", type=float, default=1.0)
    args = parser.parse_args()

    packet = build_daily_packet(
        Path(args.exports_dir),
        owner_pubkey=(args.owner_pubkey or "").strip() or None,
        prefer_cached_receipt=not bool(args.no_reuse_cached_receipt),
        rpc_retry_attempts=args.rpc_retry_attempts,
        rpc_retry_backoff_seconds=args.rpc_retry_backoff_seconds,
    )
    Path(args.output_json).write_text(json.dumps(packet, indent=2, default=str), encoding="utf-8")
    Path(args.output_md).write_text(_to_markdown(packet), encoding="utf-8")
    out = {"ok": bool(packet.get("ok")), "output_json": args.output_json, "output_md": args.output_md}
    if args.pack_dir:
        out["run_bundle"] = autopack_run_bundle(packet, Path(args.exports_dir), Path(args.pack_dir))
    print(json.dumps(out, separators=(",", ":"), default=str))
    return 0 if packet.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
