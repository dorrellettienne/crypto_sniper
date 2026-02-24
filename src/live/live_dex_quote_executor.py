import json
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import urlopen


class LiveDexQuoteError(Exception):
    pass


def _default_http_get_json(url: str, params: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    qs = urlencode({k: v for k, v in params.items() if v is not None})
    full_url = f"{url}?{qs}" if qs else url
    with urlopen(full_url, timeout=timeout_seconds) as resp:  # nosec - controlled URL from config, quote-only endpoint
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise LiveDexQuoteError("DEX quote response must be a JSON object")
    return data


class QuoteOnlyDexExecutor:
    """
    Quote-only DEX wrapper: builds order preview payloads from quote responses.
    No transaction submission is implemented.
    """

    def __init__(
        self,
        quote_url: str,
        timeout_seconds: float = 5.0,
        transport: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
        quote_only_mode: bool = True,
    ):
        self.quote_url = str(quote_url or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self.quote_only_mode = bool(quote_only_mode)
        if not self.quote_url:
            raise ValueError("quote_url is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._transport = transport or _default_http_get_json

    def get_quote_preview(self, *, input_mint: str, output_mint: str, amount: int, slippage_bps: int = 50) -> dict[str, Any]:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": int(amount),
            "slippageBps": int(slippage_bps),
        }
        try:
            payload = self._transport(self.quote_url, params, self.timeout_seconds)
        except Exception as exc:
            raise LiveDexQuoteError(f"DEX quote request failed: {exc}") from exc

        preview = {
            "provider": "quote_only_dex",
            "quote_url": self.quote_url,
            "input_mint": input_mint,
            "output_mint": output_mint,
            "amount": int(amount),
            "slippage_bps": int(slippage_bps),
            "raw_quote": payload,
        }
        if "outAmount" in payload:
            preview["out_amount"] = payload.get("outAmount")
        route_plan = payload.get("routePlan")
        if isinstance(route_plan, list):
            preview["route_count"] = len(route_plan)
        return preview

    def build_buy_order(self, token_address: str, symbol: str, entry_price: float, usd_size: float) -> dict[str, Any]:
        if not self.quote_only_mode:
            raise LiveDexQuoteError("QuoteOnlyDexExecutor requires quote_only_mode=True")
        quote = self.get_quote_preview(
            input_mint="USDC",
            output_mint=str(token_address),
            amount=max(1, int(round(float(usd_size) * 1_000_000))),
        )
        return {
            "action": "buy",
            "mode": "quote_only",
            "token_address": token_address,
            "symbol": symbol,
            "entry_price": float(entry_price),
            "usd_size": float(usd_size),
            "quote_preview": quote,
        }

    def build_sell_order(self, position_id: int, exit_price: float) -> dict[str, Any]:
        if not self.quote_only_mode:
            raise LiveDexQuoteError("QuoteOnlyDexExecutor requires quote_only_mode=True")
        return {
            "action": "sell",
            "mode": "quote_only",
            "position_id": int(position_id),
            "exit_price": float(exit_price),
            "quote_preview": {"provider": "quote_only_dex", "note": "sell quote not implemented in skeleton"},
        }

    def build_stop_loss_order(self, position_id: int, stop_percent: float) -> dict[str, Any]:
        if not self.quote_only_mode:
            raise LiveDexQuoteError("QuoteOnlyDexExecutor requires quote_only_mode=True")
        return {
            "action": "stop_loss",
            "mode": "quote_only",
            "position_id": int(position_id),
            "stop_percent": float(stop_percent),
            "quote_preview": {"provider": "quote_only_dex", "note": "stop-loss quote not implemented in skeleton"},
        }

    def build_submit_preview(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]:
        return {
            "mode": "submit_skeleton",
            "provider": "quote_only_dex",
            "client_order_id": str(client_order_id),
            "quote_only_mode": self.quote_only_mode,
            "order_action": (order_preview or {}).get("action"),
            "ready": False,
            "reason": "quote_only_executor_no_send",
        }
