import json

import pytest

from src.live.policy_profiles import get_policy_profile, load_policy_profiles, validate_policy_profile


def test_validate_policy_profile_normalizes_lists_and_numbers():
    profile = validate_policy_profile(
        {
            "name": "x",
            "token_allowlist": ["a", " b "],
            "token_blocklist": [],
            "symbol_allowlist": ["sol", " usdc "],
            "min_usd_size": 10,
            "max_usd_size": 100,
            "token_cooldown_calls": 2,
        }
    )
    assert profile["token_allowlist"] == ["a", "b"]
    assert profile["symbol_allowlist"] == ["SOL", "USDC"]
    assert profile["min_usd_size"] == 10.0


def test_validate_policy_profile_rejects_bad_bounds():
    with pytest.raises(ValueError):
        validate_policy_profile({"name": "x", "min_usd_size": 200, "max_usd_size": 100})


def test_load_and_get_policy_profiles(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps({"profiles": [{"name": "p1", "token_cooldown_calls": 1}, {"name": "p2"}]}),
        encoding="utf-8",
    )
    profiles = load_policy_profiles(str(path))
    assert len(profiles) == 2
    assert get_policy_profile("p1", str(path))["token_cooldown_calls"] == 1


def test_get_policy_profile_rejects_missing(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([{"name": "only"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        get_policy_profile("missing", str(path))
