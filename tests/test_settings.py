from config.settings import Settings


def test_default_values_when_env_missing(monkeypatch):
    for key in [
        "PAPER_MODE",
        "MAX_POSITION_MINUTES",
        "MAX_CONCURRENT_POSITIONS",
        "MAX_DAILY_LOSS",
        "RPC_URL",
        "SOLANA_RPC_URL",
        "JUPITER_BASE_URL",
        "JUPITER_QUOTE_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = Settings()

    assert settings.paper_mode is True
    assert isinstance(settings.max_position_minutes, int)
    assert isinstance(settings.max_concurrent_positions, int)
    assert isinstance(settings.max_daily_loss, (int, float))


def test_boolean_parsing_for_paper_mode(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "false")
    settings = Settings()
    assert settings.paper_mode is False

    monkeypatch.setenv("PAPER_MODE", "True")
    settings = Settings()
    assert settings.paper_mode is True


def test_numeric_env_parsing(monkeypatch):
    monkeypatch.setenv("MAX_POSITION_MINUTES", "45")
    monkeypatch.setenv("MAX_CONCURRENT_POSITIONS", "7")
    monkeypatch.setenv("MAX_DAILY_LOSS", "150")

    settings = Settings()

    assert settings.max_position_minutes == 45
    assert settings.max_concurrent_positions == 7
    assert settings.max_daily_loss == 150


def test_required_url_fields_present(monkeypatch):
    monkeypatch.setenv("RPC_URL", "https://test.rpc")
    monkeypatch.setenv("JUPITER_BASE_URL", "https://test.jupiter")
    monkeypatch.setenv("JUPITER_QUOTE_PATH", "/quote")

    settings = Settings()

    assert settings.rpc_url == "https://test.rpc"
    assert settings.jupiter_base_url == "https://test.jupiter"
    assert settings.jupiter_quote_path == "/quote"
