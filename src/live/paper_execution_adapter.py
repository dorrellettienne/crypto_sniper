from src.execution.paper_engine import simulate_buy, simulate_sell, simulate_stop_loss
from src.live.interfaces import ExecutionAdapter, ExecutionResult


class PaperExecutionAdapter(ExecutionAdapter):
    """
    Thin adapter boundary around existing paper execution functions.
    No business logic changes.
    """

    def buy(self, token_address: str, symbol: str, entry_price: float, usd_size: float) -> ExecutionResult:
        position_id = simulate_buy(
            token_address=token_address,
            symbol=symbol,
            entry_price=entry_price,
            usd_size=usd_size,
        )
        return ExecutionResult(
            ok=position_id is not None,
            action="buy",
            position_id=position_id,
            message="" if position_id is not None else "buy blocked or failed",
        )

    def sell(self, position_id: int, exit_price: float) -> ExecutionResult:
        pnl = simulate_sell(position_id, exit_price)
        return ExecutionResult(
            ok=pnl is not None,
            action="sell",
            position_id=position_id,
            pnl=pnl,
            message="" if pnl is not None else "sell failed",
        )

    def stop_loss(self, position_id: int, stop_percent: float) -> ExecutionResult:
        pnl = simulate_stop_loss(position_id, stop_percent)
        return ExecutionResult(
            ok=pnl is not None,
            action="stop_loss",
            position_id=position_id,
            pnl=pnl,
            message="" if pnl is not None else "stop loss failed",
        )
