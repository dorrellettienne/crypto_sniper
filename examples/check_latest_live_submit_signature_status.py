import json
import time
import argparse
from pathlib import Path
from urllib.request import Request, urlopen


RPC_URL = "https://api.mainnet-beta.solana.com"


def _rpc(method: str, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = Request(RPC_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=15) as resp:  # nosec - trusted RPC URL
        return json.loads(resp.read().decode("utf-8"))


def _status_value(status_result: dict | None):
    if not isinstance(status_result, dict):
        return None
    value = status_result.get("value")
    if isinstance(value, list) and value:
        return value[0]
    return None


def _status_fields(status_result: dict | None) -> dict:
    item = _status_value(status_result)
    if not isinstance(item, dict):
        return {"confirmation_status": None, "err": None, "slot": None}
    return {
        "confirmation_status": item.get("confirmationStatus"),
        "err": item.get("err"),
        "slot": item.get("slot"),
    }


def _terminal_or_better(status_result: dict | None, require_finalized: bool) -> bool:
    item = _status_value(status_result)
    if not isinstance(item, dict):
        return False
    if item.get("err") not in (None, {}):
        return True
    cs = str(item.get("confirmationStatus") or "")
    if require_finalized:
        return cs == "finalized"
    return cs in {"confirmed", "finalized"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check latest live submit signature status and fetch transaction.")
    parser.add_argument("--exports-dir", default="data/exports")
    parser.add_argument("--poll-attempts", type=int, default=1, help="Poll getSignatureStatuses this many times before getTransaction.")
    parser.add_argument("--poll-interval-seconds", type=float, default=0.75)
    parser.add_argument("--require-finalized", action="store_true", help="Poll until finalized (or attempts exhausted).")
    parser.add_argument("--summary-only", action="store_true", help="Print compact status summary instead of full payload.")
    args = parser.parse_args()

    exports = Path(args.exports_dir)
    logs = sorted(exports.glob("live_pilot_service_auto_window_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        print(json.dumps({"ok": False, "reason": "no_audit_logs_found"}))
        return 1
    log_path = logs[0]
    latest_sig = None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        payload = row.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        summary = payload.get("live_pilot_summary") or {}
        if isinstance(summary, dict):
            sig = summary.get("submitted_signature")
            if isinstance(sig, str) and sig.strip():
                latest_sig = sig.strip()
        sig2 = payload.get("submitted_signature")
        if isinstance(sig2, str) and sig2.strip():
            latest_sig = sig2.strip()
    if not latest_sig:
        print(json.dumps({"ok": False, "reason": "no_submitted_signature_found", "log": str(log_path)}))
        return 2

    try:
        import base58  # type: ignore
        decoded_len = len(base58.b58decode(latest_sig))
    except Exception:
        decoded_len = None

    status = None
    attempts_used = 0
    max_attempts = max(1, int(args.poll_attempts))
    for i in range(1, max_attempts + 1):
        attempts_used = i
        status = _rpc("getSignatureStatuses", [[latest_sig], {"searchTransactionHistory": True}])
        if _terminal_or_better((status or {}).get("result") if isinstance(status, dict) else None, require_finalized=bool(args.require_finalized)):
            break
        if i < max_attempts and float(args.poll_interval_seconds) > 0:
            time.sleep(float(args.poll_interval_seconds))
    tx = _rpc("getTransaction", [latest_sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}])
    out = {
        "ok": True,
        "log": str(log_path),
        "signature": latest_sig,
        "signature_len": len(latest_sig),
        "decoded_len": decoded_len,
        "status_result": status.get("result"),
        "tx_result": tx.get("result"),
        "status_error": status.get("error"),
        "tx_error": tx.get("error"),
        "status_summary": _status_fields(status.get("result") if isinstance(status, dict) else None),
        "polling": {
            "attempts_used": attempts_used,
            "max_attempts": max_attempts,
            "poll_interval_seconds": float(args.poll_interval_seconds),
            "require_finalized": bool(args.require_finalized),
        },
    }
    if args.summary_only:
        summary = {
            "ok": True,
            "log": str(log_path),
            "signature": latest_sig,
            "status_summary": out["status_summary"],
            "tx_present": out["tx_result"] is not None,
            "status_error": out["status_error"],
            "tx_error": out["tx_error"],
            "polling": out["polling"],
        }
        print(json.dumps(summary, separators=(",", ":"), default=str))
    else:
        print(json.dumps(out, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
