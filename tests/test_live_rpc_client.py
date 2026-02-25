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


def test_http_rpc_client_parses_mint_authorities_from_json_parsed_account():
    def transport(url, payload, timeout):
        assert payload["method"] == "getAccountInfo"
        return {
            "jsonrpc": "2.0",
            "result": {
                "value": {
                    "data": {
                        "parsed": {
                            "info": {
                                "mintAuthority": None,
                                "freezeAuthority": "FREEZE_AUTH",
                                "supply": "1000000",
                                "decimals": 6,
                            }
                        }
                    }
                }
            },
        }

    client = HttpRpcClient("https://rpc.example", transport=transport)
    out = client.get_parsed_mint_authorities("MINT1")
    assert out["mint_authority"] is None
    assert out["freeze_authority"] == "FREEZE_AUTH"
    assert out["supply"] == "1000000"
    assert out["decimals"] == 6


def test_http_rpc_client_raises_for_missing_parsed_mint_info():
    client = HttpRpcClient(
        "https://rpc.example",
        transport=lambda url, payload, timeout: {"jsonrpc": "2.0", "result": {"value": {"data": {}}}},
    )
    with pytest.raises(LiveRpcClientError):
        client.get_parsed_mint_authorities("MINT1")


def test_http_rpc_client_get_signature_status_parses_first_value():
    calls = []

    def transport(url, payload, timeout):
        calls.append(payload)
        assert payload["method"] == "getSignatureStatuses"
        return {
            "jsonrpc": "2.0",
            "result": {
                "value": [
                    {
                        "slot": 123,
                        "confirmationStatus": "confirmed",
                        "confirmations": None,
                        "err": None,
                    }
                ]
            },
        }

    client = HttpRpcClient("https://rpc.example", transport=transport)
    out = client.get_signature_status("SIG123")
    assert out["slot"] == 123
    assert out["confirmationStatus"] == "confirmed"
    assert calls[0]["params"][0] == ["SIG123"]
    assert calls[0]["params"][1]["searchTransactionHistory"] is True


def test_http_rpc_client_get_signature_status_none_when_missing():
    client = HttpRpcClient(
        "https://rpc.example",
        transport=lambda url, payload, timeout: {"jsonrpc": "2.0", "result": {"value": [None]}},
    )
    assert client.get_signature_status("SIG123") is None


def test_http_rpc_client_get_transaction_returns_result_object_or_none():
    calls = []

    def transport(url, payload, timeout):
        calls.append(payload)
        if payload["method"] == "getTransaction":
            return {
                "jsonrpc": "2.0",
                "result": {
                    "slot": 999,
                    "transaction": {"signatures": ["SIG123"]},
                    "meta": {"fee": 5000, "err": None},
                },
            }
        return {"jsonrpc": "2.0", "result": None}

    client = HttpRpcClient("https://rpc.example", transport=transport)
    out = client.get_transaction("SIG123")
    assert out is not None
    assert out["slot"] == 999
    cfg = calls[0]["params"][1]
    assert cfg["encoding"] == "jsonParsed"
    assert cfg["commitment"] == "confirmed"
    assert cfg["maxSupportedTransactionVersion"] == 0


def test_http_rpc_client_get_transaction_raises_on_rpc_error():
    client = HttpRpcClient(
        "https://rpc.example",
        transport=lambda url, payload, timeout: {"jsonrpc": "2.0", "error": {"message": "boom"}},
    )
    with pytest.raises(LiveRpcClientError):
        client.get_transaction("SIG123")


def test_http_rpc_client_send_raw_transaction_parses_signature_and_params():
    calls = []

    def transport(url, payload, timeout):
        calls.append(payload)
        assert payload["method"] == "sendTransaction"
        return {"jsonrpc": "2.0", "result": "SIG_SENT_123"}

    client = HttpRpcClient("https://rpc.example", transport=transport)
    sig = client.send_raw_transaction(
        "BASE64_TX",
        skip_preflight=True,
        max_retries=2,
        preflight_commitment="confirmed",
    )
    assert sig == "SIG_SENT_123"
    assert calls[0]["params"][0] == "BASE64_TX"
    cfg = calls[0]["params"][1]
    assert cfg["encoding"] == "base64"
    assert cfg["skipPreflight"] is True
    assert cfg["maxRetries"] == 2
    assert cfg["preflightCommitment"] == "confirmed"


def test_http_rpc_client_send_raw_transaction_raises_on_missing_result():
    client = HttpRpcClient(
        "https://rpc.example",
        transport=lambda url, payload, timeout: {"jsonrpc": "2.0", "result": None},
    )
    with pytest.raises(LiveRpcClientError):
        client.send_raw_transaction("BASE64_TX")
