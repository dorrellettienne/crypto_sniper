from config.settings import settings


if __name__ == "__main__":
    print("=== Solana Sniper Bot ===")
    print(f"RPC URL: {settings.solana_rpc_url}")
    print(f"Jupiter URL: {settings.jupiter_base_url}")
    print(f"Paper Mode: {settings.paper_mode}")