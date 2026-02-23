import requests


JUPITER_QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"


def route_exists(input_mint: str, output_mint: str, amount: int) -> bool:
    print("[ROUTE CHECK] Checking route...")

    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": 50,
    }

    try:
        response = requests.get(JUPITER_QUOTE_URL, params=params, timeout=10)
    except requests.RequestException:
        print("[ROUTE CHECK] No route found")
        return False

    if response.status_code != 200:
        print("[ROUTE CHECK] No route found")
        return False

    try:
        data = response.json()
    except ValueError:
        print("[ROUTE CHECK] No route found")
        return False

    routes = data.get("data")
    if isinstance(routes, list) and len(routes) > 0:
        print("[ROUTE CHECK] Route found")
        return True

    route_plan = data.get("routePlan")
    if isinstance(route_plan, list) and len(route_plan) > 0:
        print("[ROUTE CHECK] Route found")
        return True

    print("[ROUTE CHECK] No route found")
    return False
