import argparse
import json


def evaluate_live_send_pause_reset(*, required_token: str, provided_token: str) -> dict:
    req = str(required_token or "").strip()
    got = str(provided_token or "").strip()
    if not req:
        return {"ok": False, "reason": "missing_required_token"}
    if not got:
        return {"ok": False, "reason": "missing_provided_token"}
    if req != got:
        return {"ok": False, "reason": "token_mismatch"}
    return {
        "ok": True,
        "reason": "reset_approved",
        "adapter_config_patch": {
            "live_send_pause_reset_required_token": req,
            "live_send_pause_reset_provided_token": got,
        },
    }


def _main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--required-token", default="")
    p.add_argument("--provided-token", default="")
    args = p.parse_args()
    out = evaluate_live_send_pause_reset(required_token=args.required_token, provided_token=args.provided_token)
    print(json.dumps(out, sort_keys=True))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main())

