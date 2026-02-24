import hashlib
from dataclasses import dataclass
from pathlib import Path


def build_client_order_id(
    action: str,
    token_address: str,
    symbol: str,
    entry_price: float,
    usd_size: float,
    sequence: int = 1,
) -> str:
    raw = f"{action}|{token_address}|{symbol}|{float(entry_price):.12f}|{float(usd_size):.12f}|{int(sequence)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"coid_{action}_{digest}"


def build_request_fingerprint(
    action: str,
    token_address: str,
    symbol: str,
    entry_price: float,
    usd_size: float,
) -> str:
    raw = f"{action}|{token_address}|{symbol}|{float(entry_price):.12f}|{float(usd_size):.12f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class IdempotencyDecision:
    allowed: bool
    key: str
    reason: str = ""


class InMemoryIdempotencyStore:
    """
    Simple in-memory duplicate suppression store for dry-run/pre-live workflows.
    """

    def __init__(self):
        self._seen: set[str] = set()

    def decide_once(self, key: str) -> IdempotencyDecision:
        if key in self._seen:
            return IdempotencyDecision(allowed=False, key=key, reason="duplicate_request")
        self._seen.add(key)
        return IdempotencyDecision(allowed=True, key=key, reason="")

    def reset(self) -> None:
        self._seen.clear()


class FileBackedIdempotencyStore:
    """
    Simple file-backed duplicate suppression store (one key per line).
    Intended for pre-live/live restart resilience without adding external dependencies.
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                key = line.strip()
                if key:
                    self._seen.add(key)
        else:
            self.path.write_text("", encoding="utf-8")

    def decide_once(self, key: str) -> IdempotencyDecision:
        key = str(key)
        if key in self._seen:
            return IdempotencyDecision(allowed=False, key=key, reason="duplicate_request")
        self._seen.add(key)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(key + "\n")
        return IdempotencyDecision(allowed=True, key=key, reason="")

    def reset(self) -> None:
        self._seen.clear()
        self.path.write_text("", encoding="utf-8")
