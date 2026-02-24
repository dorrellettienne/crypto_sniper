from dataclasses import dataclass


@dataclass
class CostEstimate:
    notional_usd: float
    fee_bps: float
    slippage_bps: float
    network_fee_usd: float
    estimated_fee_usd: float
    estimated_slippage_usd: float
    total_cost_usd: float


def _validate_non_negative(name: str, value: float) -> float:
    value = float(value)
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def estimate_trade_costs(
    notional_usd: float,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    network_fee_usd: float = 0.0,
) -> CostEstimate:
    """
    Estimate one-leg trade costs using simple basis-point + flat-fee assumptions.
    """
    notional_usd = _validate_non_negative("notional_usd", notional_usd)
    fee_bps = _validate_non_negative("fee_bps", fee_bps)
    slippage_bps = _validate_non_negative("slippage_bps", slippage_bps)
    network_fee_usd = _validate_non_negative("network_fee_usd", network_fee_usd)

    estimated_fee_usd = round(notional_usd * (fee_bps / 10_000.0), 6)
    estimated_slippage_usd = round(notional_usd * (slippage_bps / 10_000.0), 6)
    total_cost_usd = round(estimated_fee_usd + estimated_slippage_usd + network_fee_usd, 6)

    return CostEstimate(
        notional_usd=notional_usd,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        network_fee_usd=network_fee_usd,
        estimated_fee_usd=estimated_fee_usd,
        estimated_slippage_usd=estimated_slippage_usd,
        total_cost_usd=total_cost_usd,
    )


def estimate_round_trip_costs(
    entry_notional_usd: float,
    exit_notional_usd: float | None = None,
    fee_bps_per_leg: float = 0.0,
    slippage_bps_per_leg: float = 0.0,
    network_fee_usd_per_leg: float = 0.0,
) -> dict:
    """
    Estimate entry + exit trade costs using the same simple assumptions per leg.
    """
    entry_notional_usd = _validate_non_negative("entry_notional_usd", entry_notional_usd)
    if exit_notional_usd is None:
        exit_notional_usd = entry_notional_usd
    exit_notional_usd = _validate_non_negative("exit_notional_usd", exit_notional_usd)

    entry = estimate_trade_costs(
        notional_usd=entry_notional_usd,
        fee_bps=fee_bps_per_leg,
        slippage_bps=slippage_bps_per_leg,
        network_fee_usd=network_fee_usd_per_leg,
    )
    exit_leg = estimate_trade_costs(
        notional_usd=exit_notional_usd,
        fee_bps=fee_bps_per_leg,
        slippage_bps=slippage_bps_per_leg,
        network_fee_usd=network_fee_usd_per_leg,
    )
    total_cost_usd = round(entry.total_cost_usd + exit_leg.total_cost_usd, 6)

    return {
        "entry": entry,
        "exit": exit_leg,
        "total_cost_usd": total_cost_usd,
    }


def apply_costs_to_pnl(gross_pnl: float, total_cost_usd: float) -> float:
    gross_pnl = float(gross_pnl)
    total_cost_usd = _validate_non_negative("total_cost_usd", total_cost_usd)
    return round(gross_pnl - total_cost_usd, 6)


def estimate_net_pnl_from_gross(
    gross_pnl: float,
    entry_notional_usd: float,
    exit_notional_usd: float | None = None,
    fee_bps_per_leg: float = 0.0,
    slippage_bps_per_leg: float = 0.0,
    network_fee_usd_per_leg: float = 0.0,
) -> dict:
    """
    Convenience helper that returns gross pnl, estimated round-trip costs, and estimated net pnl.
    """
    round_trip = estimate_round_trip_costs(
        entry_notional_usd=entry_notional_usd,
        exit_notional_usd=exit_notional_usd,
        fee_bps_per_leg=fee_bps_per_leg,
        slippage_bps_per_leg=slippage_bps_per_leg,
        network_fee_usd_per_leg=network_fee_usd_per_leg,
    )
    net_pnl = apply_costs_to_pnl(gross_pnl, round_trip["total_cost_usd"])
    return {
        "gross_pnl": round(float(gross_pnl), 6),
        "estimated_total_cost_usd": round_trip["total_cost_usd"],
        "estimated_net_pnl": net_pnl,
        "cost_breakdown": round_trip,
    }


def estimate_costs_from_quote_preview(
    quote_preview: dict | None,
    *,
    fee_bps: float = 30.0,
    slippage_bps: float = 50.0,
    network_fee_usd: float = 0.1,
    fallback_notional_usd: float | None = None,
) -> dict:
    """
    Estimate one-leg trade costs using a quote preview payload when available.
    This is a quote-aware approximation for live/pre-live metadata (not final fill accounting).
    """
    quote_preview = dict(quote_preview or {})
    raw_quote = quote_preview.get("raw_quote")
    if not isinstance(raw_quote, dict):
        raw_quote = {}

    notional_usd = None
    amount = quote_preview.get("amount")
    if amount not in (None, ""):
        try:
            # QuoteOnlyDexExecutor encodes buy input amount in USDC micro-units.
            notional_usd = float(amount) / 1_000_000.0
        except (TypeError, ValueError):
            notional_usd = None

    if notional_usd is None and fallback_notional_usd is not None:
        notional_usd = float(fallback_notional_usd)

    if notional_usd is None:
        return {
            "ok": False,
            "reason": "missing_notional",
            "estimated_total_cost_usd": 0.0,
            "estimated_fee_usd": 0.0,
            "estimated_slippage_usd": 0.0,
            "network_fee_usd": float(network_fee_usd),
            "fee_bps": float(fee_bps),
            "slippage_bps": float(slippage_bps),
            "route_count": int(quote_preview.get("route_count", 0) or 0),
        }

    route_count = int(quote_preview.get("route_count", 0) or 0)
    # Simple proxy: add a small slippage penalty for multi-route complexity.
    route_slippage_bps = max(0, route_count - 1) * 5.0
    effective_slippage_bps = float(slippage_bps) + route_slippage_bps

    est = estimate_trade_costs(
        notional_usd=notional_usd,
        fee_bps=float(fee_bps),
        slippage_bps=effective_slippage_bps,
        network_fee_usd=float(network_fee_usd),
    )

    out_amount = raw_quote.get("outAmount", quote_preview.get("out_amount"))
    return {
        "ok": True,
        "reason": "",
        "notional_usd": round(notional_usd, 6),
        "fee_bps": float(fee_bps),
        "slippage_bps_base": float(slippage_bps),
        "slippage_bps_route_penalty": route_slippage_bps,
        "slippage_bps_effective": effective_slippage_bps,
        "network_fee_usd": float(network_fee_usd),
        "estimated_fee_usd": est.estimated_fee_usd,
        "estimated_slippage_usd": est.estimated_slippage_usd,
        "estimated_total_cost_usd": est.total_cost_usd,
        "route_count": route_count,
        "out_amount": None if out_amount in (None, "") else str(out_amount),
        "provider": quote_preview.get("provider"),
        "estimate_label": "quote_aware_cost_estimate_v1",
    }
