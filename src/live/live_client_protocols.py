from typing import Protocol, runtime_checkable, Any


@runtime_checkable
class RpcClientProtocol(Protocol):
    def health_check(self) -> dict[str, Any]: ...
    def get_recent_blockhash(self) -> str: ...
    def build_confirm_preview(self, client_order_id: str) -> dict[str, Any]: ...


@runtime_checkable
class DexExecutorProtocol(Protocol):
    def build_buy_order(self, token_address: str, symbol: str, entry_price: float, usd_size: float) -> dict[str, Any]: ...
    def build_sell_order(self, position_id: int, exit_price: float) -> dict[str, Any]: ...
    def build_stop_loss_order(self, position_id: int, stop_percent: float) -> dict[str, Any]: ...
    def build_submit_preview(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]: ...
    def build_submit_request_stub(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]: ...
    def build_unsigned_submit_stub(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]: ...
    def build_signed_submit_stub(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]: ...


class NoopRpcClient:
    """
    Skeleton RPC client wrapper for live adapter integration wiring tests.
    """

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "client": "noop_rpc"}

    def get_recent_blockhash(self) -> str:
        return "NOOP_BLOCKHASH"

    def build_confirm_preview(self, client_order_id: str) -> dict[str, Any]:
        return {
            "mode": "confirm_skeleton",
            "client": "noop_rpc",
            "client_order_id": client_order_id,
            "status": "not_submitted",
        }


class NoopDexExecutor:
    """
    Skeleton DEX executor wrapper for live adapter integration wiring tests.
    """

    def build_buy_order(self, token_address: str, symbol: str, entry_price: float, usd_size: float) -> dict[str, Any]:
        return {
            "action": "buy",
            "token_address": token_address,
            "symbol": symbol,
            "entry_price": entry_price,
            "usd_size": usd_size,
        }

    def build_sell_order(self, position_id: int, exit_price: float) -> dict[str, Any]:
        return {"action": "sell", "position_id": position_id, "exit_price": exit_price}

    def build_stop_loss_order(self, position_id: int, stop_percent: float) -> dict[str, Any]:
        return {"action": "stop_loss", "position_id": position_id, "stop_percent": stop_percent}

    def build_submit_preview(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]:
        return {
            "mode": "submit_skeleton",
            "client_order_id": client_order_id,
            "order_action": order_preview.get("action"),
            "ready": False,
            "reason": "noop_executor_preview_only",
        }

    def build_submit_request_stub(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]:
        return {
            "mode": "submit_request_stub",
            "client_order_id": str(client_order_id),
            "order_action": (order_preview or {}).get("action"),
            "ready": False,
            "send_enabled": False,
            "reason": "noop_executor_submit_stub_only",
        }

    def build_unsigned_submit_stub(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]:
        return {
            "mode": "unsigned_submit_stub",
            "client_order_id": str(client_order_id),
            "order_action": (order_preview or {}).get("action"),
            "unsigned_transaction_base64": None,
            "ready": False,
            "reason": "noop_executor_no_unsigned_tx",
        }

    def build_signed_submit_stub(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]:
        return {
            "mode": "signed_submit_stub",
            "client_order_id": str(client_order_id),
            "order_action": (order_preview or {}).get("action"),
            "transaction_base64": None,
            "ready": False,
            "reason": "noop_executor_no_signed_tx",
        }
