import json

import pytest

from src.live.token_safety_profiles import (
    get_token_safety_profile,
    load_token_safety_profiles,
    validate_token_safety_profile,
)


def test_validate_token_safety_profile_normalizes_values():
    p = validate_token_safety_profile(
        {
            "name": "x",
            "token_allowlist": ["A", " B "],
            "token_blocklist": [],
            "min_token_age_seconds": 10,
            "min_liquidity_usd": 1000,
        }
    )
    assert p["name"] == "x"
    assert p["token_allowlist"] == ["A", "B"]
    assert p["min_token_age_seconds"] == 10.0


def test_validate_token_safety_profile_rejects_invalid_values():
    with pytest.raises(ValueError):
        validate_token_safety_profile({"name": ""})
    with pytest.raises(ValueError):
        validate_token_safety_profile({"name": "x", "min_token_age_seconds": -1})


def test_load_and_get_token_safety_profiles(tmp_path):
    path = tmp_path / "safety_profiles.json"
    path.write_text(
        json.dumps({"profiles": [{"name": "p1", "min_token_age_seconds": 5}, {"name": "p2"}]}),
        encoding="utf-8",
    )
    profiles = load_token_safety_profiles(str(path))
    assert len(profiles) == 2
    assert get_token_safety_profile("p1", str(path))["min_token_age_seconds"] == 5.0


def test_get_token_safety_profile_rejects_missing(tmp_path):
    path = tmp_path / "safety_profiles.json"
    path.write_text(json.dumps([{"name": "only"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        get_token_safety_profile("missing", str(path))
