from src.live.interfaces import ExecutionAdapter, ExecutionResult
from src.live.execution_realism import build_execution_realism_config, simulate_buy_execution_realism


class DryRunExecutionAdapter(ExecutionAdapter):
    """
    Simulated execution adapter for pre-live testing.
    Produces structured execution results without touching exchanges.
    """

    def __init__(self, fail_actions: set[str] | None = None, execution_realism: dict | None = None):
        self._fail_actions = set(fail_actions or set())
        self._execution_realism = build_execution_realism_config(execution_realism)

    def buy(self, token_address: str, symbol: str, entry_price: float, usd_size: float) -> ExecutionResult:
        if "buy" in self._fail_actions:
            return ExecutionResult(ok=False, action="buy", message="dry-run simulated failure")
        realism = None
        if self._execution_realism.enabled:
            realism = simulate_buy_execution_realism(
                token_address=token_address,
                symbol=symbol,
                entry_price=entry_price,
                usd_size=usd_size,
                config=self._execution_realism,
            )
            if not realism["allowed"]:
                return ExecutionResult(
                    ok=False,
                    action="buy",
                    message=f"dry-run {realism['reason']}",
                    metadata={
                        "token_address": token_address,
                        "symbol": symbol,
                        "entry_price": entry_price,
                        "usd_size": usd_size,
                        "execution_realism": realism,
                        "execution_outcome_class": realism["outcome_class"],
                    },
                )
        return ExecutionResult(
            ok=True,
            action="buy",
            position_id=1,
            message=(
                "dry-run buy accepted"
                if not realism
                else ("dry-run buy partially filled" if realism["outcome_class"] == "partial_fill" else "dry-run buy filled")
            ),
            metadata={
                "token_address": token_address,
                "symbol": symbol,
                "entry_price": entry_price,
                "usd_size": usd_size,
                "execution_outcome_class": (realism["outcome_class"] if realism else "full_fill"),
                "execution_realism": realism,
            },
        )

    def sell(self, position_id: int, exit_price: float) -> ExecutionResult:
        if "sell" in self._fail_actions:
            return ExecutionResult(ok=False, action="sell", position_id=position_id, message="dry-run simulated failure")
        return ExecutionResult(
            ok=True,
            action="sell",
            position_id=position_id,
            pnl=0.0,
            message="dry-run sell accepted",
            metadata={"exit_price": exit_price},
        )

    def stop_loss(self, position_id: int, stop_percent: float) -> ExecutionResult:
        if "stop_loss" in self._fail_actions:
            return ExecutionResult(ok=False, action="stop_loss", position_id=position_id, message="dry-run simulated failure")
        return ExecutionResult(
            ok=True,
            action="stop_loss",
            position_id=position_id,
            pnl=0.0,
            message="dry-run stop loss accepted",
            metadata={"stop_percent": stop_percent},
        )
