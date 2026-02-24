import json
from pathlib import Path


DEFAULT_POLICY_PROFILES_PATH = "config/prelive_policy_profiles.json"
DEFAULT_POLICY_PROFILE_NAME = "default_open"

POLICY_PROFILE_KEYS = {
    "name",
    "token_allowlist",
    "token_blocklist",
    "symbol_allowlist",
    "min_usd_size",
    "max_usd_size",
    "token_cooldown_calls",
}


def _normalize_str_list(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("policy list fields must be arrays")
    return [str(v).strip() for v in value if str(v).strip()]


def validate_policy_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("policy profile must be an object")
    name = str(profile.get("name") or "").strip()
    if not name:
        raise ValueError("policy profile missing name")

    out = {"name": name}
    out["token_allowlist"] = _normalize_str_list(profile.get("token_allowlist"))
    out["token_blocklist"] = _normalize_str_list(profile.get("token_blocklist"))
    out["symbol_allowlist"] = [s.upper() for s in _normalize_str_list(profile.get("symbol_allowlist"))]

    min_usd = profile.get("min_usd_size")
    max_usd = profile.get("max_usd_size")
    out["min_usd_size"] = None if min_usd is None else float(min_usd)
    out["max_usd_size"] = None if max_usd is None else float(max_usd)
    if out["min_usd_size"] is not None and out["min_usd_size"] <= 0:
        raise ValueError("min_usd_size must be > 0")
    if out["max_usd_size"] is not None and out["max_usd_size"] <= 0:
        raise ValueError("max_usd_size must be > 0")
    if out["min_usd_size"] is not None and out["max_usd_size"] is not None and out["min_usd_size"] > out["max_usd_size"]:
        raise ValueError("min_usd_size must be <= max_usd_size")

    cooldown = profile.get("token_cooldown_calls", 0)
    out["token_cooldown_calls"] = int(cooldown)
    if out["token_cooldown_calls"] < 0:
        raise ValueError("token_cooldown_calls must be >= 0")
    return out


def load_policy_profiles(path: str = DEFAULT_POLICY_PROFILES_PATH) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) and "profiles" in payload else payload
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("policy profiles file must contain a non-empty profiles list")
    return [validate_policy_profile(dict(profile)) for profile in profiles]


def get_policy_profile(profile_name: str | None = None, profiles_path: str = DEFAULT_POLICY_PROFILES_PATH) -> dict:
    profiles = load_policy_profiles(profiles_path)
    selected_name = profile_name or DEFAULT_POLICY_PROFILE_NAME
    for profile in profiles:
        if profile["name"] == selected_name:
            return profile
    raise ValueError(f"policy profile not found: {selected_name}")
