from src.jupiter import JupiterClient


def has_jupiter_route(
    client: JupiterClient,
    input_mint: str,
    output_mint: str,
    amount: int,
    slippage_bps: int,
) -> bool:
    return client.route_exists(input_mint, output_mint, amount, slippage_bps)
