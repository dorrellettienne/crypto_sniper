import json
from typing import Any, Callable
from urllib.request import Request, urlopen


class LiveRpcClientError(Exception):
    pass


def _default_json_rpc_transport(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    req = Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout_seconds) as resp:  # nosec - controlled URL from config, quote/read-only path
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise LiveRpcClientError("RPC response must be a JSON object")
    return data


class HttpRpcClient:
    """
    Read-only RPC wrapper for health and recent blockhash calls.
    Network use is injectable for deterministic tests.
    """

    def __init__(
        self,
        rpc_url: str,
        timeout_seconds: float = 5.0,
        transport: Callable[[str, dict[str, Any], float], dict[str, Any]] | None = None,
    ):
        self.rpc_url = str(rpc_url or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        if not self.rpc_url:
            raise ValueError("rpc_url is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._transport = transport or _default_json_rpc_transport

    def _rpc(self, method: str, params: list[Any] | None = None) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        try:
            data = self._transport(self.rpc_url, payload, self.timeout_seconds)
        except Exception as exc:
            raise LiveRpcClientError(f"RPC request failed: {exc}") from exc
        return data

    def health_check(self) -> dict[str, Any]:
        data = self._rpc("getHealth")
        if "error" in data:
            return {"ok": False, "client": "http_rpc", "error": data["error"]}
        return {"ok": True, "client": "http_rpc", "result": data.get("result", "ok")}

    def get_recent_blockhash(self) -> str:
        data = self._rpc("getLatestBlockhash", [{"commitment": "processed"}])
        if "error" in data:
            raise LiveRpcClientError(f"RPC returned error for blockhash: {data['error']}")
        value = (((data.get("result") or {}).get("value") or {}).get("blockhash"))
        if not value:
            raise LiveRpcClientError("missing blockhash in RPC response")
        return str(value)

    def build_confirm_preview(self, client_order_id: str) -> dict[str, Any]:
        return {
            "mode": "confirm_skeleton",
            "client": "http_rpc",
            "client_order_id": str(client_order_id),
            "status": "confirmation_not_implemented",
        }
