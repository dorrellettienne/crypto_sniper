import json
import os
import sys
from pathlib import Path


def _load_keypair_bytes_from_env() -> tuple[bytes | None, str]:
    key_b58 = os.getenv("SOLANA_PILOT_PRIVATE_KEY_B58", "").strip()
    keypair_path = os.getenv("SOLANA_PILOT_KEYPAIR_JSON_PATH", "").strip()
    if key_b58:
        try:
            import base58  # type: ignore
        except Exception:
            return None, "missing_base58_dependency"
        try:
            return bytes(base58.b58decode(key_b58)), "env_b58"
        except Exception:
            return None, "invalid_base58_private_key"
    if keypair_path:
        p = Path(keypair_path)
        if not p.exists():
            return None, "keypair_file_not_found"
        try:
            arr = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(arr, list):
                return None, "invalid_keypair_file_format"
            raw = bytes(int(x) & 0xFF for x in arr)
        except Exception:
            return None, "invalid_keypair_file_json"
        return raw, str(p)
    return None, "missing_signer_secret_env"


def _sign_with_solders(unsigned_tx_b64: str, keypair_bytes: bytes) -> tuple[str | None, str]:
    try:
        import base64
        from solders.keypair import Keypair  # type: ignore
        from solders.transaction import VersionedTransaction  # type: ignore
    except Exception:
        return None, "solders_not_installed"
    try:
        raw_tx = base64.b64decode(str(unsigned_tx_b64))
    except Exception:
        return None, "invalid_unsigned_transaction_base64"
    try:
        kp = Keypair.from_bytes(keypair_bytes)
    except Exception:
        return None, "invalid_keypair_bytes"
    try:
        unsigned_tx = VersionedTransaction.from_bytes(raw_tx)
    except Exception:
        return None, "invalid_unsigned_transaction_bytes"
    try:
        signed_tx = VersionedTransaction(unsigned_tx.message, [kp])
        signed_b64 = base64.b64encode(bytes(signed_tx)).decode("utf-8")
        return signed_b64, ""
    except Exception:
        return None, "solders_signing_failed"


def _fail(reason: str, *, code: int = 1) -> int:
    print(json.dumps({"reason": reason}))
    return code


def main() -> int:
    try:
        req = json.load(sys.stdin)
    except Exception as exc:
        return _fail(f"invalid_json_input: {exc}")

    context = req.get("context") or {}
    unsigned_submit = context.get("unsigned_submit") or {}
    unsigned_tx_b64 = unsigned_submit.get("unsigned_transaction_base64")
    if not unsigned_tx_b64:
        return _fail("missing_unsigned_transaction_base64")

    # Use a dedicated tiny pilot wallet only.
    # Do not use your main wallet secret here.
    #
    # Recommended env vars for a local signer process:
    #   SOLANA_PILOT_PRIVATE_KEY_B58   (base58-encoded secret key; requires `base58` package)
    # or
    #   SOLANA_PILOT_KEYPAIR_JSON_PATH (path to Solana keypair JSON file)
    keypair_bytes, source = _load_keypair_bytes_from_env()
    if keypair_bytes is None:
        return _fail(str(source), code=2)

    signed_b64, sign_reason = _sign_with_solders(str(unsigned_tx_b64), keypair_bytes)
    if not signed_b64:
        print(
            json.dumps(
                {
                    "reason": sign_reason or "solana_signing_failed",
                    "meta": {
                        "client_order_id": req.get("client_order_id"),
                        "order_action": ((req.get("order_preview") or {}).get("action")),
                        "unsigned_submit_ready": bool(unsigned_submit.get("ready", False)),
                        "template": "solana_command_signer_template.py",
                        "signer_secret_source": source,
                    },
                }
            )
        )
        return 3

    print(
        json.dumps(
            {
                "transaction_base64": signed_b64,
                "reason": "",
                "meta": {
                    "client_order_id": req.get("client_order_id"),
                    "order_action": ((req.get("order_preview") or {}).get("action")),
                    "unsigned_submit_ready": bool(unsigned_submit.get("ready", False)),
                    "template": "solana_command_signer_template.py",
                    "signer_secret_source": source,
                    "signing_backend": "solders",
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
