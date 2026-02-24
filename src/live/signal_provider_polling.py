from collections.abc import Callable
from typing import Any

from src.live.interfaces import SignalProvider, TradeSignal
from src.live.signal_provider_file import _to_signal


class PollingSignalProvider(SignalProvider):
    """
    Polling-style signal provider skeleton for pre-live/live-path integration.
    Uses an injected fetcher callback and buffers results between polls.
    """

    def __init__(
        self,
        fetcher: Callable[[], Any],
        swallow_fetch_errors: bool = True,
    ):
        self._fetcher = fetcher
        self._swallow_fetch_errors = bool(swallow_fetch_errors)
        self._buffer: list[TradeSignal] = []
        self.poll_count = 0
        self.fetch_errors = 0
        self.last_error: str = ""

    def _normalize_fetched(self, payload: Any) -> list[TradeSignal]:
        if payload is None:
            return []
        if isinstance(payload, TradeSignal):
            return [payload]
        if isinstance(payload, list):
            out: list[TradeSignal] = []
            for item in payload:
                if isinstance(item, TradeSignal):
                    out.append(item)
                else:
                    out.append(_to_signal(item))
            return out
        if isinstance(payload, dict):
            if "signals" in payload:
                signals = payload["signals"]
                if not isinstance(signals, list):
                    raise ValueError("fetcher payload 'signals' must be a list")
                return self._normalize_fetched(signals)
            return [_to_signal(payload)]
        raise ValueError("unsupported fetcher payload type")

    def poll(self) -> int:
        self.poll_count += 1
        try:
            payload = self._fetcher()
            signals = self._normalize_fetched(payload)
            self._buffer.extend(signals)
            return len(signals)
        except Exception as exc:
            self.fetch_errors += 1
            self.last_error = str(exc)
            if self._swallow_fetch_errors:
                return 0
            raise

    def get_next_signal(self) -> TradeSignal | None:
        if not self._buffer:
            self.poll()
        if not self._buffer:
            return None
        return self._buffer.pop(0)
