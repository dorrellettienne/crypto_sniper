from config.settings import settings
from src.execution.persistence import get_open_position_count, get_today_realized_pnl
from src.live.interfaces import RiskDecision, RiskEngine


class PreLiveRiskEngine(RiskEngine):
    """
    Real pre-live risk engine implementation using current settings + persistence.
    Mirrors the core paper-mode buy risk gates.
    """

    def can_buy(self, token_address: str, symbol: str, usd_size: float) -> RiskDecision:
        if usd_size <= 0:
            return RiskDecision(allowed=False, reason="invalid_usd_size")

        daily_pnl = round(get_today_realized_pnl(), 2)
        if daily_pnl <= settings.max_daily_loss:
            return RiskDecision(allowed=False, reason="max_daily_loss_reached")

        open_count = get_open_position_count()
        if open_count >= settings.max_concurrent_positions:
            return RiskDecision(allowed=False, reason="max_concurrent_positions_reached")

        return RiskDecision(allowed=True, reason="")
