from dataclasses import dataclass
from typing import Any


@dataclass
class ExecutionRealismConfig:
    enabled: bool = False
    simulated_latency_ms: int = 0
    max_quote_age_ms_at_fill: int | None = None
    fill_ratio: float = 1.0
    expected_slippage_bps: float = 0.0
    volatility_penalty_bps: float = 0.0
    latency_penalty_bps_per_100ms: float = 0.0
    max_realized_slippage_bps: float | None = None


def build_execution_realism_config(config: dict[str, Any] | None = None) -> ExecutionRealismConfig:
    cfg = dict(config or {})
    out = ExecutionRealismConfig(
        enabled=bool(cfg.get("enabled", False)),
        simulated_latency_ms=int(cfg.get("simulated_latency_ms", 0) or 0),
        max_quote_age_ms_at_fill=None if cfg.get("max_quote_age_ms_at_fill") in (None, "") else int(cfg.get("max_quote_age_ms_at_fill")),
        fill_ratio=float(cfg.get("fill_ratio", 1.0) or 0.0),
        expected_slippage_bps=float(cfg.get("expected_slippage_bps", 0.0) or 0.0),
        volatility_penalty_bps=float(cfg.get("volatility_penalty_bps", 0.0) or 0.0),
        latency_penalty_bps_per_100ms=float(cfg.get("latency_penalty_bps_per_100ms", 0.0) or 0.0),
        max_realized_slippage_bps=None if cfg.get("max_realized_slippage_bps") in (None, "") else float(cfg.get("max_realized_slippage_bps")),
    )
    if out.simulated_latency_ms < 0:
        raise ValueError("simulated_latency_ms must be >= 0")
    if out.fill_ratio < 0 or out.fill_ratio > 1:
        raise ValueError("fill_ratio must be within [0,1]")
    if out.max_quote_age_ms_at_fill is not None and out.max_quote_age_ms_at_fill < 0:
        raise ValueError("max_quote_age_ms_at_fill must be >= 0")
    if out.max_realized_slippage_bps is not None and out.max_realized_slippage_bps < 0:
        raise ValueError("max_realized_slippage_bps must be >= 0")
    return out


def simulate_buy_execution_realism(
    *,
    token_address: str,
    symbol: str,
    entry_price: float,
    usd_size: float,
    config: ExecutionRealismConfig,
) -> dict[str, Any]:
    quote_age_ms_at_fill = int(config.simulated_latency_ms)
    latency_penalty_bps = (float(config.simulated_latency_ms) / 100.0) * float(config.latency_penalty_bps_per_100ms)
    realized_slippage_bps = round(
        float(config.expected_slippage_bps) + float(config.volatility_penalty_bps) + float(latency_penalty_bps),
        6,
    )
    expected_token_amount = 0.0 if float(entry_price) <= 0 else round(float(usd_size) / float(entry_price), 12)
    fill_ratio = float(config.fill_ratio)
    realized_fill_ratio = max(0.0, min(1.0, fill_ratio))
    realized_token_amount = round(
        expected_token_amount * realized_fill_ratio * max(0.0, 1.0 - (realized_slippage_bps / 10_000.0)),
        12,
    )
    outcome_class = "full_fill" if realized_fill_ratio >= 0.999999 else ("partial_fill" if realized_fill_ratio > 0 else "no_fill")
    allowed = True
    reason = ""
    if config.max_quote_age_ms_at_fill is not None and quote_age_ms_at_fill > int(config.max_quote_age_ms_at_fill):
        allowed = False
        outcome_class = "stale_quote_reject"
        reason = "stale_quote_reject"
        realized_fill_ratio = 0.0
        realized_token_amount = 0.0
    elif config.max_realized_slippage_bps is not None and realized_slippage_bps > float(config.max_realized_slippage_bps):
        allowed = False
        outcome_class = "slippage_tolerance_exceeded"
        reason = "slippage_tolerance_exceeded"
        realized_fill_ratio = 0.0
        realized_token_amount = 0.0
    elif realized_fill_ratio <= 0:
        allowed = False
        outcome_class = "no_fill"
        reason = "no_fill"

    return {
        "allowed": allowed,
        "reason": reason,
        "outcome_class": outcome_class,
        "fill_ratio": round(realized_fill_ratio, 6),
        "expected_token_amount": expected_token_amount,
        "realized_token_amount": realized_token_amount,
        "simulated_latency_ms": int(config.simulated_latency_ms),
        "quote_age_ms_at_fill": quote_age_ms_at_fill,
        "expected_slippage_bps": float(config.expected_slippage_bps),
        "volatility_penalty_bps": float(config.volatility_penalty_bps),
        "latency_penalty_bps": round(float(latency_penalty_bps), 6),
        "realized_slippage_bps": realized_slippage_bps,
        "token_address": str(token_address),
        "symbol": str(symbol),
        "entry_price": float(entry_price),
        "usd_size_requested": float(usd_size),
    }
