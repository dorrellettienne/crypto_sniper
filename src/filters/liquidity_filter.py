LAMPORTS_PER_SOL = 1_000_000_000
ASSUMED_SOL_USD_PRICE = 100.0


def _estimate_liquidity_usd(quote_response: dict) -> float:
    try:
        in_amount = int(quote_response.get("inAmount", 0))
        out_amount = int(quote_response.get("outAmount", 0))
    except (TypeError, ValueError):
        return 0.0

    if in_amount <= 0 or out_amount <= 0:
        return 0.0

    sol_amount = in_amount / LAMPORTS_PER_SOL
    return sol_amount * ASSUMED_SOL_USD_PRICE


def check_min_liidity(quote_response: dict, min_usd_liquidity: float) -> bool:
    liquidity_usd = _estimate_liquidity_usd(quote_response)
    return liquidity_usd >= float(min_usd_liquidity)
