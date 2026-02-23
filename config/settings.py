import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")
MIN_USD_LIQUIDITY = 10000
MAX_CONCURRENT_POSITIONS = 3
MAX_DAILY_LOSS = -50.0
JUPITER_BASE_URL = "https://quote-api.jup.ag"
JUPITER_QUOTE_PATH = "/v6/quote"
DEFAULT_SLIPPAGE_BPS = 200
MAX_POSITION_MINUTES = 60


class Settings:
    def __init__(self) -> None:
        self.rpc_url = os.getenv("RPC_URL", os.getenv("SOLANA_RPC_URL", ""))
        self.solana_rpc_url = self.rpc_url
        self.jupiter_base_url = os.getenv("JUPITER_BASE_URL", JUPITER_BASE_URL)
        self.jupiter_quote_path = os.getenv("JUPITER_QUOTE_PATH", JUPITER_QUOTE_PATH)
        self.paper_mode = os.getenv("PAPER_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.min_usd_liquidity = float(os.getenv("MIN_USD_LIQUIDITY", str(MIN_USD_LIQUIDITY)))
        self.max_position_minutes = int(os.getenv("MAX_POSITION_MINUTES", str(MAX_POSITION_MINUTES)))
        self.max_concurrent_positions = int(os.getenv("MAX_CONCURRENT_POSITIONS", str(MAX_CONCURRENT_POSITIONS)))
        self.max_daily_loss = float(os.getenv("MAX_DAILY_LOSS", str(MAX_DAILY_LOSS)))


settings = Settings()

