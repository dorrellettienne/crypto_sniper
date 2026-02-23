import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.filters.route_check import route_exists


if __name__ == "__main__":
    sol_mint = "So11111111111111111111111111111111111111112"
    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    result = route_exists(usdc_mint, sol_mint, 1_000_000)
    print(result)
