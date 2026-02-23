from __future__ import annotations

from typing import Any, Optional

import requests


class JupiterQuoteError(Exception):
    """Raised when a Jupiter quote request fails or returns invalid data."""


class JupiterClient:
    def __init__(
        self,
        base_url: str,
        quote_path: str = "/v6/quote",
        timeout_s: float = 10.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.quote_path = quote_path if quote_path.startswith("/") else f"/{quote_path}"
        self.timeout_s = timeout_s
        self.session = session or requests.Session()

    def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{self.quote_path}"
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "slippageBps": slippage_bps,
        }

        try:
            response = self.session.get(url, params=params, timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise JupiterQuoteError(f"Jupiter quote request failed: {exc}") from exc

        status_code = getattr(response, "status_code", None)
        if status_code != 200:
            raise JupiterQuoteError(f"Jupiter quote request returned status {status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise JupiterQuoteError("Jupiter quote response contained invalid JSON") from exc

        if not isinstance(data, dict):
            raise JupiterQuoteError("Jupiter quote response must be a JSON object")

        has_out_amount = "outAmount" in data and data.get("outAmount") not in (None, "")
        route_plan = data.get("routePlan")
        has_route_plan = isinstance(route_plan, list) and len(route_plan) > 0

        if not (has_out_amount or has_route_plan):
            raise JupiterQuoteError(
                "Jupiter quote response missing expected fields: outAmount or non-empty routePlan"
            )

        return data

    def route_exists(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int,
    ) -> bool:
        try:
            self.get_quote(input_mint, output_mint, amount, slippage_bps)
            return True
        except JupiterQuoteError:
            return False
