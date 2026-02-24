import json
from pathlib import Path


DEFAULT_TOKEN_SAFETY_PROFILES_PATH = "config/token_safety_profiles.json"
DEFAULT_TOKEN_SAFETY_PROFILE_NAME = "default_open"


def _normalize_str_list(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("token safety list fields must be arrays")
    return [str(v).strip() for v in value if str(v).strip()]


def validate_token_safety_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("token safety profile must be an object")

    name = str(profile.get("name") or "").strip()
    if not name:
        raise ValueError("token safety profile missing name")

    out = {"name": name}
    out["token_allowlist"] = _normalize_str_list(profile.get("token_allowlist"))
    out["token_blocklist"] = _normalize_str_list(profile.get("token_blocklist"))

    min_age = profile.get("min_token_age_seconds", None)
    min_liq = profile.get("min_liquidity_usd", None)
    out["min_token_age_seconds"] = None if min_age is None else float(min_age)
    out["min_liquidity_usd"] = None if min_liq is None else float(min_liq)

    if out["min_token_age_seconds"] is not None and out["min_token_age_seconds"] < 0:
        raise ValueError("min_token_age_seconds must be >= 0")
    if out["min_liquidity_usd"] is not None and out["min_liquidity_usd"] < 0:
        raise ValueError("min_liquidity_usd must be >= 0")

    return out


def load_token_safety_profiles(path: str = DEFAULT_TOKEN_SAFETY_PROFILES_PATH) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) and "profiles" in payload else payload
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("token safety profiles file must contain a non-empty profiles list")
    return [validate_token_safety_profile(dict(profile)) for profile in profiles]


def get_token_safety_profile(
    profile_name: str | None = None,
    profiles_path: str = DEFAULT_TOKEN_SAFETY_PROFILES_PATH,
) -> dict:
    profiles = load_token_safety_profiles(profiles_path)
    selected = profile_name or DEFAULT_TOKEN_SAFETY_PROFILE_NAME
    for profile in profiles:
        if profile["name"] == selected:
            return profile
    raise ValueError(f"token safety profile not found: {selected}")
