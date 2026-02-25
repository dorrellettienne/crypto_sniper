import json
from pathlib import Path

from src.live.interfaces import SignalProvider, TradeSignal


def _to_signal(row: dict) -> TradeSignal:
    if not isinstance(row, dict):
        raise ValueError("signal row must be an object")

    token_address = str(row.get("token_address") or "").strip()
    symbol = str(row.get("symbol") or "").strip()
    if not token_address:
        raise ValueError("signal row missing token_address")
    if not symbol:
        raise ValueError("signal row missing symbol")

    try:
        entry_price = float(row.get("entry_price"))
    except Exception as exc:
        raise ValueError("signal row invalid entry_price") from exc

    try:
        usd_size = float(row.get("usd_size"))
    except Exception as exc:
        raise ValueError("signal row invalid usd_size") from exc

    metadata = row.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        metadata = {"value": metadata}

    return TradeSignal(
        token_address=token_address,
        symbol=symbol,
        entry_price=entry_price,
        usd_size=usd_size,
        metadata=metadata,
    )


class FileSignalProvider(SignalProvider):
    """
    File-backed deterministic signal provider for pre-live dry-run service loops.
    Supports JSON arrays or JSONL (one object per line).
    """

    DEFAULT_MAX_FILE_BYTES = 1_000_000

    def __init__(self, signals: list[TradeSignal] | None = None):
        self._signals = list(signals or [])
        self._index = 0

    @classmethod
    def from_path(cls, path: str, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> "FileSignalProvider":
        p = Path(path)
        suffix = p.suffix.lower()
        file_size = p.stat().st_size
        if file_size > int(max_file_bytes):
            raise ValueError(f"signals file exceeds max_file_bytes: {file_size} > {int(max_file_bytes)}")

        if suffix == ".jsonl":
            rows = []
            with p.open("r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rows.append(json.loads(line))
        else:
            text = p.read_text(encoding="utf-8-sig")
            payload = json.loads(text)
            rows = payload.get("signals") if isinstance(payload, dict) and "signals" in payload else payload
            if not isinstance(rows, list):
                raise ValueError("JSON signals file must contain a list or {'signals': [...]} payload")

        return cls([_to_signal(row) for row in rows])

    def get_next_signal(self) -> TradeSignal | None:
        if self._index >= len(self._signals):
            return None
        signal = self._signals[self._index]
        self._index += 1
        return signal
