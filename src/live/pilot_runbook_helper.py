import argparse
from typing import Any


def evaluate_pilot_runbook_checklist(config: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(config or {})
    checks = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail or "")})

    add("candidate_preset_selected", bool(str(cfg.get("candidate_preset_name", "") or "").strip()))
    allowlist = cfg.get("token_allowlist") or []
    add("token_allowlist_non_empty", isinstance(allowlist, list) and len([x for x in allowlist if str(x).strip()]) > 0)
    cap = cfg.get("max_order_usd_cap")
    add("max_order_usd_cap_present", cap not in (None, ""))
    if cap not in (None, ""):
        try:
            capf = float(cap)
        except Exception:
            add("max_order_usd_cap_tiny", False, "invalid cap")
        else:
            add("max_order_usd_cap_tiny", capf > 0 and capf <= float(cfg.get("pilot_hard_max_order_usd_cap", 25.0)))
    add("pilot_mode_true", bool(cfg.get("pilot_mode", False)))
    add("live_kill_switch_false", not bool(cfg.get("live_kill_switch", False)))
    add("audit_log_path_configured", bool(str(cfg.get("audit_log_path", "") or "").strip()))
    add("operator_kill_switch_known", bool(cfg.get("operator_kill_switch_ack", False)))

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks}


def _cli() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidate-preset-name", default="")
    p.add_argument("--token-allowlist", default="")
    p.add_argument("--max-order-usd-cap", type=float, default=None)
    p.add_argument("--pilot-hard-max-order-usd-cap", type=float, default=25.0)
    p.add_argument("--pilot-mode", action="store_true")
    p.add_argument("--live-kill-switch", action="store_true")
    p.add_argument("--audit-log-path", default="")
    p.add_argument("--operator-kill-switch-ack", action="store_true")
    args = p.parse_args()
    result = evaluate_pilot_runbook_checklist(
        {
            "candidate_preset_name": args.candidate_preset_name,
            "token_allowlist": [x.strip() for x in str(args.token_allowlist or "").split(",") if x.strip()],
            "max_order_usd_cap": args.max_order_usd_cap,
            "pilot_hard_max_order_usd_cap": args.pilot_hard_max_order_usd_cap,
            "pilot_mode": args.pilot_mode,
            "live_kill_switch": args.live_kill_switch,
            "audit_log_path": args.audit_log_path,
            "operator_kill_switch_ack": args.operator_kill_switch_ack,
        }
    )
    print("RUNBOOK_CHECK_OK" if result["ok"] else "RUNBOOK_CHECK_FAILED")
    for check in result["checks"]:
        status = "OK" if check["ok"] else "FAIL"
        print(f"{status}: {check['check']}{(' - ' + check['detail']) if check.get('detail') else ''}")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(_cli())
