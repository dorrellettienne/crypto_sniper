import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.filters.route_exists import has_jupiter_route


class FakeClient:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls = []

    def route_exists(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int) -> bool:
        self.calls.append((input_mint, output_mint, amount, slippage_bps))
        return self.result


def test_has_jupiter_route_delegates() -> None:
    client = FakeClient(True)

    result = has_jupiter_route(client, "mintA", "mintB", 1000, 200)

    assert result is True
    assert client.calls == [("mintA", "mintB", 1000, 200)]
