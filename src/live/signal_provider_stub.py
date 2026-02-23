from src.live.interfaces import SignalProvider, TradeSignal


class StubSignalProvider(SignalProvider):
    """
    Deterministic in-memory signal provider for pre-live dry-run testing.
    """

    def __init__(self, signals: list[TradeSignal] | None = None):
        self._signals = list(signals or [])
        self._index = 0

    def get_next_signal(self) -> TradeSignal | None:
        if self._index >= len(self._signals):
            return None
        signal = self._signals[self._index]
        self._index += 1
        return signal
