import pytest

from src.live.live_rpc_client import HttpRpcClient, LiveRpcClientError


def test_http_rpc_client_health_and_blockhash_parse():
    calls = []

    def transport(url, payload, timeout_seconds):
        calls.append((url, payload["method"], timeout_seconds))
        if payload["method"] == "getHealth":
            return {"jsonrpc": "2.0", "result": "ok"}
        if payload["method"] == "getLatestBlockhash":
            return {"jsonrpc": "2.0", "result": {"value": {"blockhash": "ABC123"}}}
        return {"jsonrpc": "2.0", "error": {"message": "bad method"}}

    client = HttpRpcClient("https://rpc.example", timeout_seconds=2.5, transport=transport)
    health = client.health_check()
    blockhash = client.get_recent_blockhash()

    assert health["ok"] is True
    assert health["client"] == "http_rpc"
    assert blockhash == "ABC123"
    assert calls[0][1] == "getHealth"
    assert calls[1][1] == "getLatestBlockhash"


def test_http_rpc_client_raises_for_missing_blockhash():
    client = HttpRpcClient(
        "https://rpc.example",
        transport=lambda url, payload, timeout: {"jsonrpc": "2.0", "result": {"value": {}}},
    )
    with pytest.raises(LiveRpcClientError):
        client.get_recent_blockhash()


def test_http_rpc_client_build_confirm_preview():
    client = HttpRpcClient(
        "https://rpc.example",
        transport=lambda url, payload, timeout: {"jsonrpc": "2.0", "result": "ok"},
    )
    preview = client.build_confirm_preview("coid_x")
    assert preview["mode"] == "confirm_skeleton"
    assert preview["client"] == "http_rpc"
    assert preview["client_order_id"] == "coid_x"
