import json
import os
import sys


def main() -> int:
    key_b58 = os.getenv("SOLANA_PILOT_PRIVATE_KEY_B58", "").strip()
    if not key_b58:
        print(json.dumps({"ok": False, "reason": "missing_SOLANA_PILOT_PRIVATE_KEY_B58"}))
        return 1
    try:
        import base58  # type: ignore
        from solders.keypair import Keypair  # type: ignore
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": f"missing_dependency:{type(exc).__name__}"}))
        return 2
    try:
        raw = bytes(base58.b58decode(key_b58))
    except Exception:
        print(json.dumps({"ok": False, "reason": "invalid_base58_private_key"}))
        return 3
    try:
        if len(raw) == 32:
            kp = Keypair.from_seed(raw)
        elif len(raw) == 64:
            kp = Keypair.from_bytes(raw)
        else:
            print(json.dumps({"ok": False, "reason": f"invalid_key_len:{len(raw)}"}))
            return 4
    except Exception:
        print(json.dumps({"ok": False, "reason": "invalid_key_material"}))
        return 5
    print(json.dumps({"ok": True, "pubkey": str(kp.pubkey()), "decoded_key_len": len(raw)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
