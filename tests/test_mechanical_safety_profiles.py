import json

import pytest

from src.live.mechanical_safety_profiles import (
    get_mechanical_safety_profile,
    load_mechanical_safety_profiles,
    validate_mechanical_safety_profile,
)


def test_validate_mechanical_safety_profile_normalizes_values():
    p = validate_mechanical_safety_profile(
        {
            "name": "strict",
            "require_buy_route": True,
            "require_sell_route": True,
            "fail_closed_on_check_error": True,
            "max_buy_price_impact_pct": 10,
            "min_buy_liquidity_usd": 5000,
            "sanity_probe_usd_size": 5,
        }
    )
    assert p["name"] == "strict"
    assert p["max_buy_price_impact_pct"] == 10.0
    assert p["min_buy_liquidity_usd"] == 5000.0
    assert p["sanity_probe_usd_size"] == 5.0


def test_validate_mechanical_safety_profile_rejects_bad_values():
    with pytest.raises(ValueError):
        validate_mechanical_safety_profile({"name": ""})
    with pytest.raises(ValueError):
        validate_mechanical_safety_profile({"name": "x", "min_buy_liquidity_usd": -1})
    with pytest.raises(ValueError):
        validate_mechanical_safety_profile({"name": "x", "sanity_probe_usd_size": 0})


def test_load_and_get_mechanical_safety_profiles(tmp_path):
    path = tmp_path / "mechanical_profiles.json"
    path.write_text(
        json.dumps({"profiles": [{"name": "strict", "require_sell_route": True}, {"name": "relaxed"}]}),
        encoding="utf-8",
    )
    profiles = load_mechanical_safety_profiles(str(path))
    assert len(profiles) == 2
    assert get_mechanical_safety_profile("strict", str(path))["require_sell_route"] is True
