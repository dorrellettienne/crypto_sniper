import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.filters.liquidity_filter import check_min_liidity


if __name__ == "__main__":
    min_usd_liquidity = 10000

    high_liquidity_quote = {
        "inAmount": str(100 * 1_000_000_000),
        "outAmount": "1000000000",
    }
    low_liquidity_quote = {
        "inAmount": str(1_000_000),
        "outAmount": "1000",
    }

    if check_min_liidity(high_liquidity_quote, min_usd_liquidity):
        print("[FILTER] Passed liquidity check")
    else:
        print("[FILTER] Rejected: Low liquidity (10000 USD)")

    if check_min_liidity(low_liquidity_quote, min_usd_liquidity):
        print("[FILTER] Passed liquidity check")
    else:
        print("[FILTER] Rejected: Low liquidity (0 USD)")
