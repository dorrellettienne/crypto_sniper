import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


RPC_URL = "https://api.mainnet-beta.solana.com"


def _rpc(method: str, params: list[Any], retry_attempts: int = 4, retry_backoff_seconds: float = 1.0) -> dict[str, Any]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = Request(RPC_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    attempts = max(1, int(retry_attempts))
    backoff = max(0.0, float(retry_backoff_seconds))
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(req, timeout=20) as resp:  # nosec - trusted RPC URL
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            last_exc = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= attempts - 1:
                raise
        except URLError as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                raise
        if backoff > 0:
            time.sleep(backoff * (2 ** attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("rpc_request_failed")


def _latest_log(exports_dir: Path) -> Path | None:
    logs = sorted(exports_dir.glob("live_pilot_service_auto_window_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _find_latest_signature_and_summary(rows: list[dict[str, Any]]) -> tuple[str | None, dict[str, Any], dict[str, Any]]:
    sig = None
    live_summary: dict[str, Any] = {}
    promotion_gate: dict[str, Any] = {}
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        maybe_summary = payload.get("live_pilot_summary")
        if isinstance(maybe_summary, dict):
            live_summary = maybe_summary
            if isinstance(maybe_summary.get("submitted_signature"), str) and maybe_summary["submitted_signature"].strip():
                sig = maybe_summary["submitted_signature"].strip()
        if isinstance(payload.get("summary"), dict):
            maybe_summary2 = payload.get("summary")
            if isinstance(maybe_summary2.get("submitted_signature"), str) and maybe_summary2["submitted_signature"].strip():
                sig = maybe_summary2["submitted_signature"].strip()
        if isinstance(payload.get("promotion_gate_summary"), dict):
            promotion_gate = payload["promotion_gate_summary"]
        if isinstance(payload.get("submitted_signature"), str) and payload["submitted_signature"].strip():
            sig = payload["submitted_signature"].strip()
    return sig, live_summary, promotion_gate


def _short_status(status_result: dict[str, Any] | None) -> dict[str, Any]:
    out = {"confirmation_status": None, "err": None, "slot": None}
    if not isinstance(status_result, dict):
        return out
    value = status_result.get("value")
    if not isinstance(value, list) or not value:
        return out
    item = value[0]
    if not isinstance(item, dict):
        return out
    out["confirmation_status"] = item.get("confirmationStatus")
    out["err"] = item.get("err")
    out["slot"] = item.get("slot")
    return out


def _extract_owner_token_delta(tx_result: dict[str, Any] | None, owner_pubkey: str | None) -> dict[str, int]:
    if not isinstance(tx_result, dict):
        return {}
    meta = tx_result.get("meta")
    if not isinstance(meta, dict):
        return {}
    owner_norm = str(owner_pubkey or "").strip().lower()
    pre_rows = meta.get("preTokenBalances") if isinstance(meta.get("preTokenBalances"), list) else []
    post_rows = meta.get("postTokenBalances") if isinstance(meta.get("postTokenBalances"), list) else []

    def _row_map(rows: list[Any]) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            owner = str(row.get("owner") or "")
            if owner_norm and owner.lower() != owner_norm:
                continue
            mint = str(row.get("mint") or "")
            ui = row.get("uiTokenAmount")
            if not mint or not isinstance(ui, dict):
                continue
            amount = ui.get("amount")
            try:
                amt_int = int(amount)
            except Exception:
                continue
            acct_idx = str(row.get("accountIndex") if row.get("accountIndex") is not None else "")
            out[(mint, acct_idx)] = amt_int
        return out

    pre = _row_map(pre_rows)
    post = _row_map(post_rows)
    deltas: dict[str, int] = {}
    for key in set(pre) | set(post):
        mint = key[0]
        delta = int(post.get(key, 0)) - int(pre.get(key, 0))
        if delta:
            deltas[mint] = deltas.get(mint, 0) + delta
    return deltas


def build_receipt(
    exports_dir: Path,
    owner_pubkey: str | None = None,
    rpc_retry_attempts: int = 4,
    rpc_retry_backoff_seconds: float = 1.0,
) -> dict[str, Any]:
    log = _latest_log(exports_dir)
    if log is None:
        return {"ok": False, "reason": "no_audit_logs_found", "exports_dir": str(exports_dir)}
    rows = _load_jsonl_rows(log)
    sig, live_summary, promotion_gate = _find_latest_signature_and_summary(rows)
    if not sig:
        return {"ok": False, "reason": "no_submitted_signature_found", "log": str(log)}

    status_resp = _rpc(
        "getSignatureStatuses",
        [[sig], {"searchTransactionHistory": True}],
        retry_attempts=rpc_retry_attempts,
        retry_backoff_seconds=rpc_retry_backoff_seconds,
    )
    tx_resp = _rpc(
        "getTransaction",
        [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
        retry_attempts=rpc_retry_attempts,
        retry_backoff_seconds=rpc_retry_backoff_seconds,
    )
    status_result = status_resp.get("result") if isinstance(status_resp, dict) else None
    tx_result = tx_resp.get("result") if isinstance(tx_resp, dict) else None
    status_short = _short_status(status_result if isinstance(status_result, dict) else None)

    token_deltas = _extract_owner_token_delta(tx_result if isinstance(tx_result, dict) else None, owner_pubkey)
    fee_lamports = None
    if isinstance(tx_result, dict) and isinstance(tx_result.get("meta"), dict):
        try:
            fee_lamports = int(tx_result["meta"].get("fee"))
        except Exception:
            fee_lamports = None

    receipt = {
        "ok": True,
        "log": str(log),
        "signature": sig,
        "solscan_url": f"https://solscan.io/tx/{sig}",
        "rpc_status": status_short,
        "tx_present": isinstance(tx_result, dict),
        "fee_lamports": fee_lamports,
        "owner_pubkey_filter": owner_pubkey or "",
        "owner_token_deltas_by_mint": token_deltas,
        "live_pilot_summary": live_summary,
        "promotion_gate_summary": promotion_gate,
        "status_error": status_resp.get("error") if isinstance(status_resp, dict) else None,
        "tx_error": tx_resp.get("error") if isinstance(tx_resp, dict) else None,
    }
    return receipt


def _to_markdown(receipt: dict[str, Any]) -> str:
    if not receipt.get("ok"):
        return f"# Live Submit Receipt\n\n- status: error\n- reason: {receipt.get('reason')}\n"
    rpc = dict(receipt.get("rpc_status") or {})
    summary = dict(receipt.get("live_pilot_summary") or {})
    truth = dict(summary.get("settlement_truth") or {})
    econ = dict(summary.get("economics") or {})
    lines = [
        "# Live Submit Receipt",
        "",
        f"- signature: `{receipt.get('signature')}`",
        f"- solscan: {receipt.get('solscan_url')}",
        f"- rpc_confirmation_status: `{rpc.get('confirmation_status')}`",
        f"- rpc_err: `{rpc.get('err')}`",
        f"- tx_present: `{receipt.get('tx_present')}`",
        f"- fee_lamports: `{receipt.get('fee_lamports')}`",
        f"- chain_outcome_class: `{summary.get('chain_outcome_class')}`",
        f"- chain_terminal_reason: `{summary.get('chain_terminal_reason')}`",
    ]
    if truth:
        lines.extend(
            [
                f"- truth_confidence: `{truth.get('confidence')}`",
                f"- truth_terminal_state: `{truth.get('terminal_state')}`",
                f"- truth_owner_token_delta_raw: `{truth.get('owner_token_delta_raw')}`",
            ]
        )
    if econ:
        lines.extend(
            [
                f"- realized_slippage_bps_vs_quote: `{econ.get('realized_slippage_bps_vs_quote')}`",
                f"- quote_vs_settlement_mismatch: `{econ.get('quote_vs_settlement_mismatch')}`",
                f"- quote_vs_settlement_mismatch_class: `{econ.get('quote_vs_settlement_mismatch_class')}`",
            ]
        )
    token_deltas = dict(receipt.get("owner_token_deltas_by_mint") or {})
    if token_deltas:
        lines.append("")
        lines.append("## Owner Token Deltas")
        lines.append("")
        for mint, delta in sorted(token_deltas.items()):
            lines.append(f"- `{mint}`: `{delta}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export latest live submit receipt and RPC truth summary.")
    parser.add_argument("--exports-dir", default="data/exports")
    parser.add_argument("--owner-pubkey", default="", help="Optional owner pubkey to compute token deltas for.")
    parser.add_argument("--output-json", default="", help="Optional path to write receipt JSON.")
    parser.add_argument("--output-md", default="", help="Optional path to write receipt markdown.")
    parser.add_argument("--rpc-retry-attempts", type=int, default=4)
    parser.add_argument("--rpc-retry-backoff-seconds", type=float, default=1.0)
    args = parser.parse_args()

    receipt = build_receipt(
        Path(args.exports_dir),
        owner_pubkey=(args.owner_pubkey or "").strip() or None,
        rpc_retry_attempts=args.rpc_retry_attempts,
        rpc_retry_backoff_seconds=args.rpc_retry_backoff_seconds,
    )
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    if args.output_md:
        Path(args.output_md).write_text(_to_markdown(receipt), encoding="utf-8")
    print(json.dumps(receipt, separators=(",", ":"), default=str))
    return 0 if receipt.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
