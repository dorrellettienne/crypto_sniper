import pytest

from src.live.interfaces import TradeSignal
from src.live.signal_provider_polling import PollingSignalProvider


def test_polling_signal_provider_yields_from_list_payload_and_buffers():
    state = {"n": 0}

    def fetcher():
        state["n"] += 1
        if state["n"] == 1:
            return [
                {"token_address": "A", "symbol": "A", "entry_price": 0.01, "usd_size": 10},
                {"token_address": "B", "symbol": "B", "entry_price": 0.02, "usd_size": 20},
            ]
        return []

    provider = PollingSignalProvider(fetcher)
    s1 = provider.get_next_signal()
    s2 = provider.get_next_signal()
    s3 = provider.get_next_signal()

    assert s1 is not None and s1.token_address == "A"
    assert s2 is not None and s2.token_address == "B"
    assert s3 is None
    assert provider.poll_count >= 2


def test_polling_signal_provider_accepts_single_trade_signal_instance():
    provider = PollingSignalProvider(lambda: TradeSignal(token_address="X", symbol="X", entry_price=0.01, usd_size=5))
    s = provider.get_next_signal()
    assert s is not None
    assert s.symbol == "X"


def test_polling_signal_provider_accepts_wrapper_payload():
    provider = PollingSignalProvider(
        lambda: {
            "signals": [
                {"token_address": "W1", "symbol": "W1", "entry_price": 0.01, "usd_size": 1},
                {"token_address": "W2", "symbol": "W2", "entry_price": 0.01, "usd_size": 2},
            ]
        }
    )
    assert provider.get_next_signal().token_address == "W1"
    assert provider.get_next_signal().token_address == "W2"


def test_polling_signal_provider_swallows_fetch_errors_by_default():
    provider = PollingSignalProvider(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    signal = provider.get_next_signal()
    assert signal is None
    assert provider.fetch_errors == 1
    assert provider.last_error == "boom"


def test_polling_signal_provider_can_raise_fetch_errors():
    provider = PollingSignalProvider(lambda: (_ for _ in ()).throw(RuntimeError("boom")), swallow_fetch_errors=False)
    with pytest.raises(RuntimeError):
        provider.get_next_signal()


def test_polling_signal_provider_rejects_bad_payload_type():
    provider = PollingSignalProvider(lambda: "bad")
    signal = provider.get_next_signal()
    assert signal is None
    assert provider.fetch_errors == 1
    assert "unsupported" in provider.last_error
