import json
from pathlib import Path


DEFAULT_MECHANICAL_SAFETY_PROFILES_PATH = "config/mechanical_safety_profiles.json"
DEFAULT_MECHANICAL_SAFETY_PROFILE_NAME = "strict"


def validate_mechanical_safety_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("mechanical safety profile must be an object")

    name = str(profile.get("name") or "").strip()
    if not name:
        raise ValueError("mechanical safety profile missing name")

    out = {"name": name}
    out["require_buy_route"] = bool(profile.get("require_buy_route", True))
    out["require_sell_route"] = bool(profile.get("require_sell_route", True))
    out["require_sanity_probe_route"] = bool(profile.get("require_sanity_probe_route", False))
    out["fail_closed_on_check_error"] = bool(profile.get("fail_closed_on_check_error", True))
    out["fail_closed_on_quote_error"] = bool(profile.get("fail_closed_on_quote_error", out["fail_closed_on_check_error"]))
    out["fail_closed_on_rpc_error"] = bool(profile.get("fail_closed_on_rpc_error", out["fail_closed_on_check_error"]))

    max_buy_price_impact_pct = profile.get("max_buy_price_impact_pct")
    min_buy_liquidity_usd = profile.get("min_buy_liquidity_usd")
    sanity_probe_usd_size = profile.get("sanity_probe_usd_size")
    max_quote_age_ms = profile.get("max_quote_age_ms")
    quote_max_attempts = profile.get("quote_max_attempts")
    rpc_max_attempts = profile.get("rpc_max_attempts")
    quote_retry_backoff_seconds = profile.get("quote_retry_backoff_seconds")
    rpc_retry_backoff_seconds = profile.get("rpc_retry_backoff_seconds")

    out["max_buy_price_impact_pct"] = None if max_buy_price_impact_pct is None else float(max_buy_price_impact_pct)
    out["min_buy_liquidity_usd"] = None if min_buy_liquidity_usd is None else float(min_buy_liquidity_usd)
    out["sanity_probe_usd_size"] = None if sanity_probe_usd_size is None else float(sanity_probe_usd_size)
    out["max_quote_age_ms"] = None if max_quote_age_ms is None else int(max_quote_age_ms)
    out["quote_max_attempts"] = 1 if quote_max_attempts is None else int(quote_max_attempts)
    out["rpc_max_attempts"] = 1 if rpc_max_attempts is None else int(rpc_max_attempts)
    out["quote_retry_backoff_seconds"] = 0.0 if quote_retry_backoff_seconds is None else float(quote_retry_backoff_seconds)
    out["rpc_retry_backoff_seconds"] = 0.0 if rpc_retry_backoff_seconds is None else float(rpc_retry_backoff_seconds)

    if out["max_buy_price_impact_pct"] is not None and out["max_buy_price_impact_pct"] < 0:
        raise ValueError("max_buy_price_impact_pct must be >= 0")
    if out["min_buy_liquidity_usd"] is not None and out["min_buy_liquidity_usd"] < 0:
        raise ValueError("min_buy_liquidity_usd must be >= 0")
    if out["sanity_probe_usd_size"] is not None and out["sanity_probe_usd_size"] <= 0:
        raise ValueError("sanity_probe_usd_size must be > 0")
    if out["max_quote_age_ms"] is not None and out["max_quote_age_ms"] < 0:
        raise ValueError("max_quote_age_ms must be >= 0")
    if out["quote_max_attempts"] <= 0 or out["rpc_max_attempts"] <= 0:
        raise ValueError("quote_max_attempts and rpc_max_attempts must be > 0")
    if out["quote_retry_backoff_seconds"] < 0 or out["rpc_retry_backoff_seconds"] < 0:
        raise ValueError("retry backoff seconds must be >= 0")

    return out


def load_mechanical_safety_profiles(path: str = DEFAULT_MECHANICAL_SAFETY_PROFILES_PATH) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) and "profiles" in payload else payload
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("mechanical safety profiles file must contain a non-empty profiles list")
    return [validate_mechanical_safety_profile(dict(p)) for p in profiles]


def get_mechanical_safety_profile(
    profile_name: str | None = None,
    profiles_path: str = DEFAULT_MECHANICAL_SAFETY_PROFILES_PATH,
) -> dict:
    selected = str(profile_name or DEFAULT_MECHANICAL_SAFETY_PROFILE_NAME)
    for profile in load_mechanical_safety_profiles(profiles_path):
        if profile["name"] == selected:
            return profile
    raise ValueError(f"mechanical safety profile not found: {selected}")
