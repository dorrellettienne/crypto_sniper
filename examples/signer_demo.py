import json
import sys


def main() -> int:
    try:
        req = json.load(sys.stdin)
    except Exception as exc:
        print(json.dumps({"reason": f"invalid_json_input: {exc}"}))
        return 1

    order_preview = req.get("order_preview") or {}
    context = req.get("context") or {}
    unsigned_submit = context.get("unsigned_submit") or {}
    unsigned_tx = unsigned_submit.get("unsigned_transaction_base64")
    using_unsigned = bool(unsigned_submit.get("ready")) and bool(unsigned_tx)

    # This is intentionally fake: it demonstrates the JSON contract only.
    resp = {
        # Demo behavior: if an unsigned tx is provided, echo it back to prove the handoff contract works.
        # This is not real signing.
        "transaction_base64": str(unsigned_tx) if using_unsigned else "FAKE_BASE64_SIGNED_TX_FOR_DEMO_ONLY",
        "reason": "",
        "meta": {
            "signer": "signer_demo.py",
            "client_order_id": req.get("client_order_id"),
            "order_action": order_preview.get("action"),
            "estimated_notional_usd": ((context.get("manual_request") or {}).get("estimated_notional_usd")),
            "unsigned_submit_mode": unsigned_submit.get("mode"),
            "unsigned_submit_ready": bool(unsigned_submit.get("ready", False)),
            "used_unsigned_submit_passthrough": using_unsigned,
        },
    }
    print(json.dumps(resp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
