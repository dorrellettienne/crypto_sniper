from src.live.interfaces import TradeSignal
from src.live.signal_provider_stub import StubSignalProvider


def test_stub_signal_provider_yields_signals_in_order():
    provider = StubSignalProvider(
        [
            TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=10),
            TradeSignal(token_address="B", symbol="B", entry_price=0.02, usd_size=20),
        ]
    )

    s1 = provider.get_next_signal()
    s2 = provider.get_next_signal()
    s3 = provider.get_next_signal()

    assert s1 is not None and s1.token_address == "A"
    assert s2 is not None and s2.token_address == "B"
    assert s3 is None
