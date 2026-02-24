from dataclasses import dataclass
from typing import Any


@dataclass
class LivePilotSafetyDecision:
    allowed: bool
    reason: str
    details: dict[str, Any]


def normalize_live_pilot_config(config: dict | None) -> dict:
    cfg = dict(config or {})
    normalized = dict(cfg)
    normalized["live_enabled"] = bool(cfg.get("live_enabled", False))
    normalized["pilot_mode"] = bool(cfg.get("pilot_mode", False))
    normalized["live_kill_switch"] = bool(cfg.get("live_kill_switch", False))
    normalized["pilot_require_single_position"] = bool(cfg.get("pilot_require_single_position", False))

    allowlist = cfg.get("allowlist_tokens", [])
    if allowlist is None:
        allowlist = []
    normalized["allowlist_tokens"] = [str(v).strip() for v in allowlist if str(v).strip()]

    cap = cfg.get("max_order_usd_cap")
    normalized["max_order_usd_cap"] = None if cap in (None, "") else float(cap)

    pilot_cap = cfg.get("pilot_hard_max_order_usd_cap", 25.0)
    normalized["pilot_hard_max_order_usd_cap"] = float(pilot_cap)
    if normalized["pilot_hard_max_order_usd_cap"] <= 0:
        raise ValueError("pilot_hard_max_order_usd_cap must be > 0")

    max_concurrent = cfg.get("max_concurrent_positions")
    normalized["max_concurrent_positions"] = None if max_concurrent in (None, "") else int(max_concurrent)

    normalized["audit_log_path"] = str(cfg.get("audit_log_path", "") or "").strip()
    normalized["candidate_preset_name"] = str(cfg.get("candidate_preset_name", "") or "").strip()
    return normalized


def evaluate_live_pilot_safety_gate(config: dict | None) -> LivePilotSafetyDecision:
    cfg = normalize_live_pilot_config(config)

    if not cfg["pilot_mode"]:
        return LivePilotSafetyDecision(True, "pilot_mode_disabled", {"pilot_mode": False})
    if not cfg["live_enabled"]:
        return LivePilotSafetyDecision(False, "pilot_mode_requires_live_enabled", {"live_enabled": False, "pilot_mode": True})
    if cfg["live_kill_switch"]:
        return LivePilotSafetyDecision(False, "live_kill_switch_active", {"live_kill_switch": True})
    if not cfg["allowlist_tokens"]:
        return LivePilotSafetyDecision(False, "missing_allowlist", {"allowlist_tokens": []})

    cap = cfg["max_order_usd_cap"]
    if cap is None:
        return LivePilotSafetyDecision(False, "missing_max_order_usd_cap", {"max_order_usd_cap": None})
    if cap <= 0:
        return LivePilotSafetyDecision(False, "invalid_max_order_usd_cap", {"max_order_usd_cap": cap})
    if cap > cfg["pilot_hard_max_order_usd_cap"]:
        return LivePilotSafetyDecision(
            False,
            "pilot_max_order_usd_cap_exceeds_pilot_hard_cap",
            {
                "max_order_usd_cap": cap,
                "pilot_hard_max_order_usd_cap": cfg["pilot_hard_max_order_usd_cap"],
            },
        )

    if not cfg["audit_log_path"]:
        return LivePilotSafetyDecision(False, "missing_audit_log_path", {"audit_log_path": ""})

    if cfg["pilot_require_single_position"] and cfg["max_concurrent_positions"] != 1:
        return LivePilotSafetyDecision(
            False,
            "pilot_requires_single_position_limit",
            {"max_concurrent_positions": cfg["max_concurrent_positions"]},
        )

    return LivePilotSafetyDecision(
        True,
        "",
        {
            "mode": "pilot_live",
            "candidate_preset_name": cfg["candidate_preset_name"] or "unspecified",
            "allowlist_count": len(cfg["allowlist_tokens"]),
            "max_order_usd_cap": cap,
            "pilot_hard_max_order_usd_cap": cfg["pilot_hard_max_order_usd_cap"],
            "audit_log_path": cfg["audit_log_path"],
            "pilot_require_single_position": cfg["pilot_require_single_position"],
            "max_concurrent_positions": cfg["max_concurrent_positions"],
        },
    )


def build_live_startup_guardrails(config: dict | None) -> dict:
    cfg = normalize_live_pilot_config(config)
    mode = "pilot_live" if (cfg["live_enabled"] and cfg["pilot_mode"]) else ("live" if cfg["live_enabled"] else "prelive_dry_run")
    return {
        "mode": mode,
        "pilot_mode": cfg["pilot_mode"],
        "candidate_preset_name": cfg["candidate_preset_name"] or "unspecified",
        "allowlist_count": len(cfg["allowlist_tokens"]),
        "max_order_usd_cap": cfg["max_order_usd_cap"],
        "audit_log_path": cfg["audit_log_path"] or None,
        "live_kill_switch": cfg["live_kill_switch"],
        "pilot_hard_max_order_usd_cap": cfg["pilot_hard_max_order_usd_cap"],
    }

