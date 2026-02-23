import sys
from pathlib import Path

import requests

from storage import count_tokens, init_db, save_token

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.filters.liquidity_filter import check_min_liidity

JUPITER_QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TEST_AMOUNT_LAMPORTS = 10_000_000  # 0.01 SOL
LAMPORTS_PER_SOL = 1_000_000_000
ASSUMED_SOL_USD_PRICE = 100.0


def get_test_quote():
    params = {
        "inputMint": SOL_MINT,
        "outputMint": USDC_MINT,
        "amount": TEST_AMOUNT_LAMPORTS,
        "slippageBps": 50,
    }

    try:
        response = requests.get(JUPITER_QUOTE_URL, params=params, timeout=10)
    except requests.RequestException as exc:
        print(f"Request error: {exc}")
        return

    if response.status_code != 200:
        print(f"Jupiter API error: HTTP {response.status_code}")
        print(response.text)
        return

    try:
        data = response.json()
    except ValueError:
        print("Failed to parse JSON response")
        print(response.text)
        return

    route_plan = data.get("routePlan", [])
    print(f"Input Mint: {data.get('inputMint')}")
    print(f"Output Mint: {data.get('outputMint')}")
    print(f"In Amount: {data.get('inAmount')}")
    print(f"Out Amount: {data.get('outAmount')}")
    print(f"Route Count: {len(route_plan)}")

    try:
        in_amount = int(data.get("inAmount", 0))
        liquidity_usd = (in_amount / LAMPORTS_PER_SOL) * ASSUMED_SOL_USD_PRICE
    except (TypeError, ValueError):
        liquidity_usd = 0.0

    if check_min_liidity(data, settings.min_usd_liquidity):
        print("[FILTER] Passed liquidity check")
    else:
        print(f"[FILTER] Rejected: Low liquidity ({liquidity_usd:.2f} USD)")
        return

    discovered_tokens = [
        {
            "mint_address": data.get("inputMint", SOL_MINT),
            "symbol": "SOL",
            "name": "Solana",
        },
        {
            "mint_address": data.get("outputMint", USDC_MINT),
            "symbol": "USDC",
            "name": "USD Coin",
        },
    ]

    print(f"Tokens fetched from Jupiter: {len(discovered_tokens)}")
    for token in discovered_tokens:
        save_token(token["mint_address"], token["symbol"], token["name"])

    print(f"Total tokens stored: {count_tokens()}")


if __name__ == "__main__":
    init_db()
    get_test_quote()
