import json
from pathlib import Path
from urllib.request import Request, urlopen


RPC_URL = "https://api.mainnet-beta.solana.com"


def _rpc(method: str, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = Request(RPC_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=15) as resp:  # nosec - trusted RPC URL
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    exports = Path("data/exports")
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

    status = _rpc("getSignatureStatuses", [[latest_sig], {"searchTransactionHistory": True}])
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
    }
    print(json.dumps(out, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
