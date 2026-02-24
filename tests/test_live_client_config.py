import pytest

from src.live.live_client_config import normalize_live_client_config


def test_normalize_live_client_config_defaults():
    cfg = normalize_live_client_config({})
    assert cfg["dex_quote_only_mode"] is True
    assert cfg["use_real_quote_clients"] is False
    assert cfg["rpc_timeout_seconds"] == 5.0


def test_normalize_live_client_config_rejects_invalid_timeouts():
    with pytest.raises(ValueError):
        normalize_live_client_config({"rpc_timeout_seconds": 0})
    with pytest.raises(ValueError):
        normalize_live_client_config({"dex_quote_timeout_seconds": -1})

