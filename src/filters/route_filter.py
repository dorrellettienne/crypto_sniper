import requests


JUPITER_QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
SOL_MINT = "So11111111111111111111111111111111111111112"
TEST_AMOUNT_LAMPORTS = 10_000_000


def has_jupiter_route(input_mint: str, output_mint: str, amount: int) -> bool:
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": 50,
    }

    try:
        response = requests.get(JUPITER_QUOTE_URL, params=params, timeout=5)
        response.raise_for_status()
    except requests.RequestException:
        return False

    try:
        data = response.json()
    except ValueError:
        return False

    routes = data.get("data")
    if isinstance(routes, list):
        return len(routes) > 0

    route_plan = data.get("routePlan")
    if isinstance(route_plan, list):
        return len(route_plan) > 0

    return False


def token_has_liquidity(token_mint: str) -> bool:
    return has_jupiter_route(SOL_MINT, token_mint, TEST_AMOUNT_LAMPORTS)
