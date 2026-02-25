from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class VolatilityGuardDecision:
    allowed: bool
    reason: str = ""
    derisk_applied: bool = False
    adjusted_usd_size: float | None = None
    details: dict[str, Any] | None = None


class VolatilityGuard:
    """
    Session-aware prelive volatility guard:
    - blocks on clustered losses
    - blocks on drawdown from session peak realized PnL
    - optionally de-risks size before hard block thresholds are reached
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        current_loss_streak_fn: Callable[[], int] | None = None,
        today_realized_pnl_fn: Callable[[], float] | None = None,
        max_loss_streak_block: int | None = None,
        loss_streak_derisk_threshold: int | None = None,
        max_session_drawdown_usd_block: float | None = None,
        session_drawdown_derisk_threshold_usd: float | None = None,
        derisk_size_multiplier: float = 1.0,
        derisk_min_usd_size: float | None = None,
    ):
        self.enabled = bool(enabled)
        self._current_loss_streak_fn = current_loss_streak_fn or (lambda: 0)
        self._today_realized_pnl_fn = today_realized_pnl_fn or (lambda: 0.0)
        self.max_loss_streak_block = None if max_loss_streak_block is None else int(max_loss_streak_block)
        self.loss_streak_derisk_threshold = (
            None if loss_streak_derisk_threshold is None else int(loss_streak_derisk_threshold)
        )
        self.max_session_drawdown_usd_block = (
            None if max_session_drawdown_usd_block is None else float(max_session_drawdown_usd_block)
        )
        self.session_drawdown_derisk_threshold_usd = (
            None if session_drawdown_derisk_threshold_usd is None else float(session_drawdown_derisk_threshold_usd)
        )
        self.derisk_size_multiplier = float(derisk_size_multiplier)
        self.derisk_min_usd_size = None if derisk_min_usd_size is None else float(derisk_min_usd_size)
        self._session_peak_realized_pnl: float | None = None

        if self.derisk_size_multiplier <= 0:
            raise ValueError("derisk_size_multiplier must be > 0")
        if self.derisk_min_usd_size is not None and self.derisk_min_usd_size <= 0:
            raise ValueError("derisk_min_usd_size must be > 0")

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_loss_streak_block": self.max_loss_streak_block,
            "loss_streak_derisk_threshold": self.loss_streak_derisk_threshold,
            "max_session_drawdown_usd_block": self.max_session_drawdown_usd_block,
            "session_drawdown_derisk_threshold_usd": self.session_drawdown_derisk_threshold_usd,
            "derisk_size_multiplier": self.derisk_size_multiplier,
            "derisk_min_usd_size": self.derisk_min_usd_size,
        }

    def assess(self, *, token_address: str, symbol: str, requested_usd_size: float) -> VolatilityGuardDecision:
        if not self.enabled:
            return VolatilityGuardDecision(True, "disabled", False, None, {"enabled": False})

        try:
            current_loss_streak = int(self._current_loss_streak_fn() or 0)
            realized_pnl = float(self._today_realized_pnl_fn() or 0.0)
        except Exception as exc:
            return VolatilityGuardDecision(False, "volatility_guard_check_error", False, None, {"error": str(exc)})

        if self._session_peak_realized_pnl is None:
            self._session_peak_realized_pnl = realized_pnl
        else:
            self._session_peak_realized_pnl = max(self._session_peak_realized_pnl, realized_pnl)

        drawdown_from_peak = max(0.0, float(self._session_peak_realized_pnl) - realized_pnl)
        details = {
            "token_address": str(token_address),
            "symbol": str(symbol),
            "requested_usd_size": float(requested_usd_size),
            "current_loss_streak": current_loss_streak,
            "today_realized_pnl": round(realized_pnl, 6),
            "session_peak_realized_pnl": round(float(self._session_peak_realized_pnl), 6),
            "session_drawdown_from_peak_usd": round(drawdown_from_peak, 6),
        }

        if self.max_loss_streak_block is not None and current_loss_streak >= self.max_loss_streak_block:
            return VolatilityGuardDecision(False, "loss_streak_circuit_breaker", False, None, details)

        if self.max_session_drawdown_usd_block is not None and drawdown_from_peak >= self.max_session_drawdown_usd_block:
            return VolatilityGuardDecision(False, "drawdown_circuit_breaker", False, None, details)

        derisk_reasons: list[str] = []
        if self.loss_streak_derisk_threshold is not None and current_loss_streak >= self.loss_streak_derisk_threshold:
            derisk_reasons.append("loss_streak_derisk")
        if (
            self.session_drawdown_derisk_threshold_usd is not None
            and drawdown_from_peak >= self.session_drawdown_derisk_threshold_usd
        ):
            derisk_reasons.append("drawdown_derisk")

        if derisk_reasons:
            adjusted = float(requested_usd_size) * self.derisk_size_multiplier
            if self.derisk_min_usd_size is not None:
                adjusted = max(adjusted, self.derisk_min_usd_size)
            adjusted = round(adjusted, 6)
            details["derisk_reasons"] = derisk_reasons
            details["derisk_size_multiplier"] = self.derisk_size_multiplier
            details["adjusted_usd_size"] = adjusted
            return VolatilityGuardDecision(True, "derisk_applied", True, adjusted, details)

        return VolatilityGuardDecision(True, "", False, None, details)
