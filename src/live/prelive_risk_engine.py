from config.settings import settings
from src.execution.persistence import get_open_position_count, get_today_realized_pnl
from src.live.interfaces import RiskDecision, RiskEngine


class PreLiveRiskEngine(RiskEngine):
    """
    Real pre-live risk engine implementation using current settings + persistence.
    Mirrors the core paper-mode buy risk gates.
    """

    def __init__(
        self,
        token_allowlist: list[str] | None = None,
        token_blocklist: list[str] | None = None,
        symbol_allowlist: list[str] | None = None,
        min_usd_size: float | None = None,
        max_usd_size: float | None = None,
        token_cooldown_calls: int = 0,
    ):
        self.token_allowlist = {str(v).strip() for v in (token_allowlist or []) if str(v).strip()}
        self.token_blocklist = {str(v).strip() for v in (token_blocklist or []) if str(v).strip()}
        self.symbol_allowlist = {str(v).strip().upper() for v in (symbol_allowlist or []) if str(v).strip()}
        self.min_usd_size = None if min_usd_size is None else float(min_usd_size)
        self.max_usd_size = None if max_usd_size is None else float(max_usd_size)
        self.token_cooldown_calls = max(0, int(token_cooldown_calls))
        self._call_index = 0
        self._last_seen_call_by_token: dict[str, int] = {}

    def can_buy(self, token_address: str, symbol: str, usd_size: float) -> RiskDecision:
        self._call_index += 1
        token_address = str(token_address)
        symbol = str(symbol)

        if usd_size <= 0:
            return RiskDecision(allowed=False, reason="invalid_usd_size")

        if self.min_usd_size is not None and usd_size < self.min_usd_size:
            return RiskDecision(allowed=False, reason="usd_size_below_min")
        if self.max_usd_size is not None and usd_size > self.max_usd_size:
            return RiskDecision(allowed=False, reason="usd_size_above_max")

        if self.token_allowlist and token_address not in self.token_allowlist:
            return RiskDecision(allowed=False, reason="token_not_allowlisted")
        if token_address in self.token_blocklist:
            return RiskDecision(allowed=False, reason="token_blocklisted")
        if self.symbol_allowlist and symbol.upper() not in self.symbol_allowlist:
            return RiskDecision(allowed=False, reason="symbol_not_allowlisted")

        if self.token_cooldown_calls > 0:
            last_seen = self._last_seen_call_by_token.get(token_address)
            if last_seen is not None and (self._call_index - last_seen) <= self.token_cooldown_calls:
                self._last_seen_call_by_token[token_address] = self._call_index
                return RiskDecision(allowed=False, reason="token_cooldown_active")

        daily_pnl = round(get_today_realized_pnl(), 2)
        if daily_pnl <= settings.max_daily_loss:
            return RiskDecision(allowed=False, reason="max_daily_loss_reached")

        open_count = get_open_position_count()
        if open_count >= settings.max_concurrent_positions:
            return RiskDecision(allowed=False, reason="max_concurrent_positions_reached")

        if self.token_cooldown_calls > 0:
            self._last_seen_call_by_token[token_address] = self._call_index

        return RiskDecision(allowed=True, reason="")
