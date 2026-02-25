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

    def get_signature_status(self, signature: str, *, search_transaction_history: bool = True) -> dict[str, Any] | None:
        data = self._rpc(
            "getSignatureStatuses",
            [
                [str(signature)],
                {"searchTransactionHistory": bool(search_transaction_history)},
            ],
        )
        if "error" in data:
            raise LiveRpcClientError(f"RPC returned error for getSignatureStatuses: {data['error']}")
        value = (data.get("result") or {}).get("value")
        if not isinstance(value, list):
            raise LiveRpcClientError("missing signature status list in RPC response")
        if not value:
            return None
        first = value[0]
        if first is None:
            return None
        if not isinstance(first, dict):
            raise LiveRpcClientError("invalid signature status entry in RPC response")
        return first

    def get_transaction(
        self,
        signature: str,
        *,
        encoding: str = "jsonParsed",
        commitment: str = "confirmed",
        max_supported_transaction_version: int = 0,
    ) -> dict[str, Any] | None:
        data = self._rpc(
            "getTransaction",
            [
                str(signature),
                {
                    "encoding": str(encoding),
                    "commitment": str(commitment),
                    "maxSupportedTransactionVersion": int(max_supported_transaction_version),
                },
            ],
        )
        if "error" in data:
            raise LiveRpcClientError(f"RPC returned error for getTransaction: {data['error']}")
        return data.get("result")

    def send_raw_transaction(
        self,
        transaction_base64: str,
        *,
        skip_preflight: bool = False,
        max_retries: int | None = None,
        preflight_commitment: str = "processed",
    ) -> str:
        params_cfg: dict[str, Any] = {
            "encoding": "base64",
            "skipPreflight": bool(skip_preflight),
            "preflightCommitment": str(preflight_commitment),
        }
        if max_retries is not None:
            params_cfg["maxRetries"] = int(max_retries)
        data = self._rpc(
            "sendTransaction",
            [
                str(transaction_base64),
                params_cfg,
            ],
        )
        if "error" in data:
            raise LiveRpcClientError(f"RPC returned error for sendTransaction: {data['error']}")
        result = data.get("result")
        if not result:
            raise LiveRpcClientError("missing signature result in sendTransaction response")
        return str(result)

    def get_account_info(self, pubkey: str, encoding: str = "jsonParsed") -> dict[str, Any] | None:
        data = self._rpc(
            "getAccountInfo",
            [
                str(pubkey),
                {
                    "encoding": str(encoding),
                    "commitment": "processed",
                },
            ],
        )
        if "error" in data:
            raise LiveRpcClientError(f"RPC returned error for getAccountInfo: {data['error']}")
        return (data.get("result") or {}).get("value")

    def get_parsed_mint_authorities(self, mint_address: str) -> dict[str, Any]:
        value = self.get_account_info(str(mint_address), encoding="jsonParsed")
        if not isinstance(value, dict):
            raise LiveRpcClientError("missing mint account info in RPC response")

        data = value.get("data")
        if not isinstance(data, dict):
            raise LiveRpcClientError("missing parsed mint data in RPC response")
        parsed = data.get("parsed")
        if not isinstance(parsed, dict):
            raise LiveRpcClientError("missing parsed field in mint account data")
        info = parsed.get("info")
        if not isinstance(info, dict):
            raise LiveRpcClientError("missing info field in parsed mint account data")

        return {
            "mint_authority": info.get("mintAuthority"),
            "freeze_authority": info.get("freezeAuthority"),
            "supply": info.get("supply"),
            "decimals": info.get("decimals"),
        }
