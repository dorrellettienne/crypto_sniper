from dataclasses import dataclass
from typing import Any


@dataclass
class LiveSafetyGateDecision:
    allowed: bool
    reason: str
    details: dict[str, Any]


def normalize_live_safety_config(config: dict | None) -> dict:
    cfg = dict(config or {})
    normalized = dict(cfg)
    normalized["live_enabled"] = bool(cfg.get("live_enabled", False))
    normalized["live_kill_switch"] = bool(cfg.get("live_kill_switch", False))

    allowlist = cfg.get("allowlist_tokens", [])
    if allowlist is None:
        allowlist = []
    if not isinstance(allowlist, list):
        raise ValueError("allowlist_tokens must be a list")
    normalized["allowlist_tokens"] = [str(v).strip() for v in allowlist if str(v).strip()]

    cap = cfg.get("max_order_usd_cap", None)
    if cap in (None, ""):
        normalized["max_order_usd_cap"] = None
    else:
        normalized["max_order_usd_cap"] = float(cap)

    hard_cap = cfg.get("hard_max_order_usd_cap", 1000.0)
    normalized["hard_max_order_usd_cap"] = float(hard_cap)
    if normalized["hard_max_order_usd_cap"] <= 0:
        raise ValueError("hard_max_order_usd_cap must be > 0")

    return normalized


def evaluate_live_safety_gate(config: dict | None) -> LiveSafetyGateDecision:
    cfg = normalize_live_safety_config(config)

    if not cfg["live_enabled"]:
        return LiveSafetyGateDecision(True, "live_disabled", {"live_enabled": False})

    if cfg["live_kill_switch"]:
        return LiveSafetyGateDecision(False, "live_kill_switch_active", {"live_kill_switch": True})

    if not cfg["allowlist_tokens"]:
        return LiveSafetyGateDecision(False, "missing_allowlist", {"allowlist_tokens": []})

    cap = cfg.get("max_order_usd_cap")
    if cap is None:
        return LiveSafetyGateDecision(False, "missing_max_order_usd_cap", {"max_order_usd_cap": None})
    if cap <= 0:
        return LiveSafetyGateDecision(False, "invalid_max_order_usd_cap", {"max_order_usd_cap": cap})
    if cap > cfg["hard_max_order_usd_cap"]:
        return LiveSafetyGateDecision(
            False,
            "max_order_usd_cap_exceeds_hard_cap",
            {"max_order_usd_cap": cap, "hard_max_order_usd_cap": cfg["hard_max_order_usd_cap"]},
        )

    return LiveSafetyGateDecision(
        True,
        "",
        {
            "allowlist_count": len(cfg["allowlist_tokens"]),
            "max_order_usd_cap": cap,
            "hard_max_order_usd_cap": cfg["hard_max_order_usd_cap"],
        },
    )
