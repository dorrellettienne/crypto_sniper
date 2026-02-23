from dataclasses import dataclass
from typing import Protocol, runtime_checkable, Any


@dataclass
class ExecutionResult:
    ok: bool
    action: str
    position_id: int | None = None
    pnl: float | None = None
    message: str = ""
    metadata: dict[str, Any] | None = None


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""


@runtime_checkable
class ExecutionAdapter(Protocol):
    def buy(self, token_address: str, symbol: str, entry_price: float, usd_size: float) -> ExecutionResult: ...
    def sell(self, position_id: int, exit_price: float) -> ExecutionResult: ...
    def stop_loss(self, position_id: int, stop_percent: float) -> ExecutionResult: ...


@runtime_checkable
class RiskEngine(Protocol):
    def can_buy(self, token_address: str, symbol: str, usd_size: float) -> RiskDecision: ...
