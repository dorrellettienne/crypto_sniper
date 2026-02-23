import pytest

from src.live.config_validation import validate_candidate_preset_config


def _valid_preset():
    return {
        "name": "candidate",
        "usd_size": 100,
        "stop_loss_percent": 0.12,
        "sell_price": 0.034,
        "p_buy": 0.28,
        "p_stop_loss": 0.15,
        "p_sell": 0.32,
        "p_stop_check": 0.15,
        "p_time_exit": 0.1,
    }


def test_validate_candidate_preset_config_normalizes_numbers():
    normalized = validate_candidate_preset_config(_valid_preset())
    assert normalized["usd_size"] == 100.0
    assert normalized["p_sell"] == 0.32


def test_validate_candidate_preset_config_rejects_invalid_probability_sum():
    preset = _valid_preset()
    preset["p_time_exit"] = 0.11
    with pytest.raises(ValueError):
        validate_candidate_preset_config(preset)


def test_validate_candidate_preset_config_rejects_missing_keys():
    preset = _valid_preset()
    del preset["sell_price"]
    with pytest.raises(ValueError):
        validate_candidate_preset_config(preset)
