import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.filters.route_filter import token_has_liquidity


if __name__ == "__main__":
    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    fake_mint = "FakeMint1111111111111111111111111111111111"

    print(token_has_liquidity(usdc_mint))
    print(token_has_liquidity(fake_mint))
