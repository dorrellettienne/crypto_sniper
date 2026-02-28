import json
import time
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class LiveDexQuoteError(Exception):
    pass


DEFAULT_SOLANA_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _default_http_get_json(url: str, params: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    qs = urlencode({k: v for k, v in params.items() if v is not None})
    full_url = f"{url}?{qs}" if qs else url
    with urlopen(full_url, timeout=timeout_seconds) as resp:  # nosec - controlled URL from config, quote-only endpoint
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise LiveDexQuoteError("DEX quote response must be a JSON object")
    return data


def _default_http_post_json(url: str, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    req = Request(
        str(url),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout_seconds) as resp:  # nosec - controlled URL from config, unsigned tx build only
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise LiveDexQuoteError("DEX swap response must be a JSON object")
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
        now_ms_fn: Callable[[], int] | None = None,
        swap_url: str | None = None,
        swap_timeout_seconds: float | None = None,
        swap_transport: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
        swap_user_public_key: str | None = None,
        quote_input_mint: str | None = None,
    ):
        self.quote_url = str(quote_url or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self.quote_only_mode = bool(quote_only_mode)
        self.swap_url = str(swap_url or "").strip()
        self.swap_timeout_seconds = float(swap_timeout_seconds or timeout_seconds)
        self.swap_user_public_key = str(swap_user_public_key or "").strip()
        self.quote_input_mint = str(quote_input_mint or DEFAULT_SOLANA_USDC_MINT).strip()
        if not self.quote_url:
            raise ValueError("quote_url is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.swap_url and self.swap_timeout_seconds <= 0:
            raise ValueError("swap_timeout_seconds must be > 0")
        self._transport = transport or _default_http_get_json
        self._swap_transport = swap_transport or _default_http_post_json
        self._now_ms_fn = now_ms_fn or (lambda: int(time.time() * 1000))

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
            "fetched_at_unix_ms": int(self._now_ms_fn()),
            "raw_quote": payload,
        }
        if "outAmount" in payload:
            preview["out_amount"] = payload.get("outAmount")
        route_plan = payload.get("routePlan")
        if isinstance(route_plan, list):
            preview["route_count"] = len(route_plan)
        return preview

    def build_buy_order(self, token_address: str, symbol: str, entry_price: float, usd_size: float) -> dict[str, Any]:
        quote = self.get_quote_preview(
            input_mint=self.quote_input_mint,
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
        return {
            "action": "sell",
            "mode": "quote_only",
            "position_id": int(position_id),
            "exit_price": float(exit_price),
            "quote_preview": {"provider": "quote_only_dex", "note": "sell quote not implemented in skeleton"},
        }

    def build_sell_order_for_position(
        self,
        *,
        position_id: int,
        token_address: str,
        symbol: str,
        token_amount_raw: int | None,
        entry_price: float,
        exit_price: float,
        usd_size: float,
    ) -> dict[str, Any]:
        amount = max(1, int(token_amount_raw or 0))
        quote = self.get_quote_preview(
            input_mint=str(token_address),
            output_mint=self.quote_input_mint,
            amount=amount,
        )
        return {
            "action": "sell",
            "mode": "quote_only",
            "position_id": int(position_id),
            "token_address": str(token_address),
            "symbol": str(symbol),
            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "usd_size": float(usd_size),
            "token_amount_raw": int(amount),
            "quote_preview": quote,
        }

    def build_stop_loss_order(self, position_id: int, stop_percent: float) -> dict[str, Any]:
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

    def build_submit_request_stub(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]:
        return {
            "mode": "submit_request_stub",
            "provider": "quote_only_dex",
            "client_order_id": str(client_order_id),
            "order_action": (order_preview or {}).get("action"),
            "quote_only_mode": self.quote_only_mode,
            "ready": False,
            "send_enabled": False,
            "reason": "quote_only_executor_submit_stub_no_send",
        }

    def build_unsigned_submit_stub(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]:
        action = (order_preview or {}).get("action")
        if str(action) not in {"buy", "sell", "stop_loss"}:
            return {
                "mode": "unsigned_submit_stub",
                "provider": "quote_only_dex",
                "client_order_id": str(client_order_id),
                "order_action": action,
                "unsigned_transaction_base64": None,
                "ready": False,
                "reason": "quote_only_executor_unsigned_action_not_supported",
            }
        if not self.swap_url:
            return {
                "mode": "unsigned_submit_stub",
                "provider": "quote_only_dex",
                "client_order_id": str(client_order_id),
                "order_action": action,
                "unsigned_transaction_base64": None,
                "ready": False,
                "reason": "quote_only_executor_no_swap_url",
            }
        if not self.swap_user_public_key:
            return {
                "mode": "unsigned_submit_stub",
                "provider": "quote_only_dex",
                "client_order_id": str(client_order_id),
                "order_action": action,
                "unsigned_transaction_base64": None,
                "ready": False,
                "reason": "quote_only_executor_no_swap_user_public_key",
            }
        quote_preview = (order_preview or {}).get("quote_preview") or {}
        raw_quote = quote_preview.get("raw_quote")
        if not isinstance(raw_quote, dict) or not raw_quote:
            return {
                "mode": "unsigned_submit_stub",
                "provider": "quote_only_dex",
                "client_order_id": str(client_order_id),
                "order_action": action,
                "unsigned_transaction_base64": None,
                "ready": False,
                "reason": "quote_only_executor_missing_quote_for_unsigned_tx",
            }
        body = {
            "quoteResponse": raw_quote,
            "userPublicKey": self.swap_user_public_key,
            "wrapAndUnwrapSol": True,
        }
        try:
            payload = self._swap_transport(self.swap_url, body, self.swap_timeout_seconds)
        except Exception as exc:
            return {
                "mode": "unsigned_submit_stub",
                "provider": "quote_only_dex",
                "client_order_id": str(client_order_id),
                "order_action": action,
                "unsigned_transaction_base64": None,
                "ready": False,
                "reason": "quote_only_executor_unsigned_tx_build_error",
                "error": str(exc),
            }
        unsigned_tx = payload.get("swapTransaction")
        return {
            "mode": "unsigned_submit_stub",
            "provider": "quote_only_dex",
            "client_order_id": str(client_order_id),
            "order_action": action,
            "swap_url": self.swap_url,
            "unsigned_transaction_base64": str(unsigned_tx) if unsigned_tx else None,
            "ready": bool(unsigned_tx),
            "reason": "" if unsigned_tx else "quote_only_executor_swap_response_missing_transaction",
            "swap_response_meta": {
                "last_valid_block_height": payload.get("lastValidBlockHeight"),
                "prioritization_fee_lamports": payload.get("prioritizationFeeLamports"),
            },
        }

    def build_signed_submit_stub(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]:
        return {
            "mode": "signed_submit_stub",
            "provider": "quote_only_dex",
            "client_order_id": str(client_order_id),
            "order_action": (order_preview or {}).get("action"),
            "transaction_base64": None,
            "ready": False,
            "reason": "quote_only_executor_no_signed_tx",
        }
