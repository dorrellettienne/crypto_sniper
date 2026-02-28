import pytest

from src.live.live_execution_adapter import LiveExecutionAdapter, validate_live_execution_config


def test_validate_live_execution_config_defaults_to_disabled():
    cfg = validate_live_execution_config({})
    assert cfg["live_enabled"] is False
    assert cfg["rpc_url"] == ""


def test_validate_live_execution_config_rejects_enabled_missing_keys():
    with pytest.raises(ValueError):
        validate_live_execution_config({"live_enabled": True, "rpc_url": "https://example"})


def test_live_execution_adapter_disabled_returns_guardrail_results():
    adapter = LiveExecutionAdapter()
    buy = adapter.buy("TOKEN", "SYM", 0.01, 100)
    sell = adapter.sell(1, 0.02)

    assert buy.ok is False
    assert buy.action == "buy"
    assert "disabled" in buy.message
    assert buy.metadata["usd_size"] == 100

    assert sell.ok is False
    assert sell.position_id == 1
    assert "disabled" in sell.message


def test_live_execution_adapter_enabled_is_skeleton_only():
    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
        }
    )
    result = adapter.buy("TOKEN", "SYM", 0.01, 50)
    assert result.ok is False
    assert "skeleton only" in result.message
    assert result.metadata["order_preview"]["action"] == "buy"
    assert result.metadata["rpc_health"]["ok"] is True
    assert result.metadata["client_order_id"].startswith("coid_buy_")
    assert len(result.metadata["request_fingerprint"]) > 20
    assert result.metadata["retry_policy_decision"]["decision"] in {"retry", "fail", "timeout"}
    assert result.metadata["lifecycle_events"][0]["status"] == "submitted"
    assert "reconciliation" in result.metadata
    assert result.metadata["reconciliation"]["final_status"] in {"retrying", "failed", "timeout", "confirmed"}
    assert "estimated_costs" in result.metadata
    assert result.metadata["estimated_costs"]["ok"] is True
    assert result.metadata["estimated_costs"]["estimate_label"] == "quote_aware_cost_estimate_v1"
    assert "submit_preview" not in result.metadata
    assert "confirmation_preview" not in result.metadata


def test_live_execution_adapter_enabled_blocks_on_safety_gate():
    with pytest.raises(ValueError) as exc:
        LiveExecutionAdapter(
            {
                "live_enabled": True,
                "rpc_url": "https://rpc.example",
                "wallet_public_key": "wallet_pub",
                "dex_name": "JUPITER",
                "live_kill_switch": True,
                "allowlist_tokens": ["TOKEN_A"],
                "max_order_usd_cap": 100,
            }
        )
    assert "live safety gate blocked startup" in str(exc.value)


def test_live_execution_adapter_enabled_blocks_on_pilot_safety_gate():
    with pytest.raises(ValueError) as exc:
        LiveExecutionAdapter(
            {
                "live_enabled": True,
                "pilot_mode": True,
                "rpc_url": "https://rpc.example",
                "wallet_public_key": "wallet_pub",
                "dex_name": "JUPITER",
                "allowlist_tokens": ["TOKEN_A"],
                "max_order_usd_cap": 10,
                # missing audit_log_path for pilot mode
            }
        )
    assert "live pilot safety gate blocked startup" in str(exc.value)


def test_live_execution_adapter_accepts_injected_clients_for_wiring():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}

        def get_recent_blockhash(self):
            return "STUB_HASH"

        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client": "rpc_stub", "client_order_id": client_order_id}

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "marker": "dex_stub", "token_address": token_address}

        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell", "marker": "dex_stub", "position_id": position_id}

        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss", "marker": "dex_stub", "position_id": position_id}

        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "marker": "dex_stub", "client_order_id": client_order_id}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "pilot_mode": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "pilot_hard_max_order_usd_cap": 200,
            "audit_log_path": "data/exports/audit.jsonl",
            "candidate_preset_name": "candidate_final_v1_tp_higher_034",
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    buy = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    sell = adapter.sell(1, 0.02)
    stop = adapter.stop_loss(1, 0.1)

    assert buy.metadata["order_preview"]["marker"] == "dex_stub"
    assert buy.metadata["rpc_health"]["client"] == "rpc_stub"
    assert buy.metadata["startup_guardrails"]["mode"] == "pilot_live"
    assert buy.metadata["startup_guardrails"]["candidate_preset_name"] == "candidate_final_v1_tp_higher_034"
    assert buy.metadata["lifecycle_events"][0]["action"] == "buy"
    assert "reconciliation" in buy.metadata
    assert "estimated_costs" in buy.metadata
    assert sell.metadata["order_preview"]["action"] == "sell"
    assert sell.metadata["lifecycle_events"][0]["action"] == "sell"
    assert sell.metadata["client_order_id"].startswith("coid_")
    assert "estimated_costs" in sell.metadata
    assert stop.metadata["order_preview"]["action"] == "stop_loss"
    assert stop.metadata["lifecycle_events"][0]["action"] == "stop_loss"


def test_live_execution_adapter_can_build_real_quote_wrappers_from_config():
    def rpc_transport(url, payload, timeout_seconds):
        if payload["method"] == "getHealth":
            return {"jsonrpc": "2.0", "result": "ok"}
        if payload["method"] == "getLatestBlockhash":
            return {"jsonrpc": "2.0", "result": {"value": {"blockhash": "XYZ"}}}
        return {"jsonrpc": "2.0", "error": {"message": "unsupported"}}

    def dex_transport(url, params, timeout_seconds):
        return {"outAmount": "999", "routePlan": [{"leg": 1}]}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "rpc_read_url": "https://rpc-read.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "dex_quote_url": "https://quote.example",
            "use_real_quote_clients": True,
            "dex_quote_only_mode": True,
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
        },
        rpc_transport=rpc_transport,
        dex_quote_transport=dex_transport,
    )

    result = adapter.buy("TOKEN_A", "TKA", 0.01, 10)

    assert result.ok is False
    assert result.metadata["rpc_health"]["client"] == "http_rpc"
    assert result.metadata["order_preview"]["mode"] == "quote_only"
    assert result.metadata["order_preview"]["quote_preview"]["out_amount"] == "999"
    assert result.metadata["estimated_costs"]["ok"] is True
    assert result.metadata["estimated_costs"]["out_amount"] == "999"


def test_live_execution_adapter_can_wrap_quote_executor_with_signed_submit_stub_from_config():
    def rpc_transport(url, payload, timeout_seconds):
        if payload["method"] == "getHealth":
            return {"jsonrpc": "2.0", "result": "ok"}
        if payload["method"] == "getLatestBlockhash":
            return {"jsonrpc": "2.0", "result": {"value": {"blockhash": "XYZ"}}}
        return {"jsonrpc": "2.0", "error": {"message": "unsupported"}}

    def dex_transport(url, params, timeout_seconds):
        return {"outAmount": "999", "routePlan": [{"leg": 1}]}

    def dex_swap_transport(url, body, timeout_seconds):
        return {"swapTransaction": "UNSIGNED_TX_B64"}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "rpc_read_url": "https://rpc-read.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "dex_quote_url": "https://quote.example",
            "dex_swap_url": "https://swap.example",
            "use_real_quote_clients": True,
            "dex_quote_only_mode": True,
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "dex_signed_submit_stub_tx_base64": "SIGNED_TX_B64",
        },
        rpc_transport=rpc_transport,
        dex_quote_transport=dex_transport,
        dex_swap_transport=dex_swap_transport,
    )

    buy_order = adapter.dex_executor.build_buy_order("TOKEN_A", "TKA", 0.01, 10)
    unsigned_stub = adapter.dex_executor.build_unsigned_submit_stub(buy_order, "coid_unsigned")
    assert unsigned_stub["ready"] is True
    assert unsigned_stub["unsigned_transaction_base64"] == "UNSIGNED_TX_B64"
    signed_stub = adapter.dex_executor.build_signed_submit_stub({"action": "buy"}, "coid_test")
    assert signed_stub["ready"] is True
    assert signed_stub["transaction_base64"] == "SIGNED_TX_B64"


def test_live_execution_adapter_submit_skeleton_enabled_adds_submit_confirm_metadata():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}

        def get_recent_blockhash(self):
            return "STUB_HASH"

        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id, "status": "pending"}

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": 10_000_000, "route_count": 1}}

        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}

        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}

        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id, "order_action": order_preview.get("action")}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "submit_skeleton_confirmation_outcomes": ["pending", "confirmed"],
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)

    assert result.ok is False
    assert "submit skeleton only" in result.message
    assert result.metadata["submit_skeleton_enabled"] is True
    assert result.metadata["submit_preview"]["mode"] == "submit_skeleton"
    assert result.metadata["confirmation_preview"]["mode"] == "confirm_skeleton"
    assert result.metadata["submit_workflow"]["final_decision"] == "confirmed"
    assert result.metadata["submit_workflow"]["reconciliation"]["final_status"] == "confirmed"
    assert result.metadata["submit_workflow"]["lifecycle_events"][-1]["status"] == "confirmed"
    assert result.metadata["submit_workflow"]["submit_confirm_summary"]["outcome_class"] == "submit_confirm_confirmed"


def test_live_execution_adapter_submit_skeleton_timeout_outcome():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}

        def get_recent_blockhash(self):
            return "STUB_HASH"

        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy"}

        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}

        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}

        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "submit_skeleton_outcome": "timeout",
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    assert result.metadata["submit_workflow"]["final_decision"] == "timeout"
    assert result.metadata["submit_workflow"]["reconciliation"]["final_status"] == "timeout"
    assert result.metadata["submit_workflow"]["submit_confirm_summary"]["outcome_class"] == "submit_confirm_timeout"


def test_live_execution_adapter_submit_skeleton_can_stale_reject_before_submit():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}

        def get_recent_blockhash(self):
            return "STUB_HASH"

        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy"}

        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}

        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}

        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "submit_skeleton_quote_age_ms_at_submit": 500,
            "submit_skeleton_max_quote_age_ms_before_submit": 100,
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    assert result.metadata["submit_workflow"]["final_decision"] == "stale_quote_reject"
    assert result.metadata["submit_workflow"]["reconciliation"]["final_status"] == "stale_quote_reject"
    assert result.metadata["submit_workflow"]["submit_confirm_summary"]["outcome_class"] == "submit_rejected_stale_quote"


def test_live_execution_adapter_submit_skeleton_can_attach_chain_reconciliation():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}

        def get_recent_blockhash(self):
            return "STUB_HASH"

        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy"}

        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}

        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}

        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "submit_skeleton_outcome": "confirmed",
            "submit_skeleton_signature_status_payload": {
                "value": [{"slot": 77, "confirmationStatus": "confirmed", "err": None}]
            },
            "submit_skeleton_tx_payload": {
                "result": {
                    "slot": 77,
                    "transaction": {"signatures": ["SIG777"]},
                    "meta": {
                        "fee": 5000,
                        "err": None,
                        "preBalances": [10000],
                        "postBalances": [5000],
                        "preTokenBalances": [],
                        "postTokenBalances": [
                            {
                                "accountIndex": 0,
                                "mint": "MINT_A",
                                "owner": "OWNER1",
                                "uiTokenAmount": {"amount": "20"},
                            }
                        ],
                    },
                }
            },
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    chain_rec = result.metadata["submit_workflow"]["chain_reconciliation"]
    assert chain_rec["outcome_class"] == "live_confirmed_reconciled"
    assert chain_rec["terminal_reason"] == "confirmed"


def test_live_execution_adapter_submit_skeleton_can_fetch_chain_reconciliation_via_rpc_client():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}

        def get_recent_blockhash(self):
            return "STUB_HASH"

        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}

        def get_signature_status(self, signature, search_transaction_history=True):
            assert signature == "SIG_FETCH"
            assert search_transaction_history is True
            return {"slot": 88, "confirmationStatus": "finalized", "err": None}

        def get_transaction(self, signature, **kwargs):
            assert signature == "SIG_FETCH"
            return {
                "slot": 88,
                "transaction": {"signatures": [signature]},
                "meta": {
                    "fee": 5000,
                    "err": None,
                    "preBalances": [10000],
                    "postBalances": [5000],
                    "preTokenBalances": [],
                    "postTokenBalances": [
                        {
                            "accountIndex": 0,
                            "mint": "MINT_A",
                            "owner": "OWNER1",
                            "uiTokenAmount": {"amount": "50"},
                        }
                    ],
                },
            }

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": 10_000_000, "route_count": 1}}

        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}

        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}

        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "submit_skeleton_outcome": "confirmed",
            "submit_skeleton_fetch_chain_reconciliation": True,
            "submit_skeleton_chain_signature": "SIG_FETCH",
            "submit_skeleton_chain_owner_filter": "OWNER1",
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    fetch_meta = result.metadata["submit_workflow"]["chain_reconciliation_fetch"]
    assert fetch_meta["enabled"] is True
    assert fetch_meta["status_fetched"] is True
    assert fetch_meta["tx_fetched"] is True
    assert fetch_meta["error"] == ""
    chain_rec = result.metadata["submit_workflow"]["chain_reconciliation"]
    assert chain_rec["outcome_class"] == "live_confirmed_reconciled"
    assert chain_rec["terminal_reason"] == "finalized"


def test_live_execution_adapter_submit_skeleton_fetch_chain_reconciliation_requires_signature():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}

        def get_recent_blockhash(self):
            return "STUB_HASH"

        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy"}

        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}

        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}

        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "submit_skeleton_fetch_chain_reconciliation": True,
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    fetch_meta = result.metadata["submit_workflow"]["chain_reconciliation_fetch"]
    assert fetch_meta["enabled"] is True
    assert fetch_meta["status_fetched"] is False
    assert fetch_meta["tx_fetched"] is False
    assert fetch_meta["error"] == "missing_chain_signature"


def test_live_execution_adapter_manual_submit_scaffold_rejected_without_approval_token():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": 10_000_000, "route_count": 1, "out_amount": "12345"}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE_ME",
            "manual_submit_mode": "buy_only",
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    gate = result.metadata["manual_submit_gate"]
    req = result.metadata["manual_submit_request"]
    assert gate["approved"] is False
    assert gate["reason"] == "missing_provided_token"
    assert req["ready_for_manual_submit"] is False
    assert req["approval_reason"] == "missing_provided_token"
    assert req["send_enabled"] is False
    assert req["send_reason"] == "manual_submit_scaffold_only_no_send"
    assert result.metadata["submit_dispatch"]["reason"] == "manual_submit_not_approved"
    assert result.metadata["submit_dispatch"]["attempted"] is False
    assert result.metadata["submit_dispatch"]["live_send_network_enabled"] is False


def test_live_execution_adapter_manual_submit_scaffold_approved_metadata_only():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": 10_000_000, "route_count": 2, "out_amount": "777"}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE_ME",
            "manual_submit_provided_token": "APPROVE_ME",
            "manual_submit_mode": "buy_only",
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    gate = result.metadata["manual_submit_gate"]
    req = result.metadata["manual_submit_request"]
    assert gate["approved"] is True
    assert gate["token_match"] is True
    assert req["ready_for_manual_submit"] is True
    assert req["approval_granted"] is True
    assert req["manual_submit_mode"] == "buy_only"
    assert req["estimated_notional_usd"] == 10.0
    assert req["quote_route_count"] == 2
    assert req["expected_out_amount"] == "777"
    assert req["send_enabled"] is False
    assert result.metadata["submit_dispatch"]["reason"] == "live_send_disabled"
    assert result.metadata["submit_dispatch"]["attempted"] is False
    assert result.metadata["submit_dispatch"]["live_send_network_enabled"] is False


def test_live_execution_adapter_manual_submit_dispatch_builds_submit_stub_when_live_send_enabled():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": 10_000_000, "route_count": 1}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {
                "mode": "submit_request_stub",
                "client_order_id": client_order_id,
                "order_action": order_preview.get("action"),
                "ready": False,
                "send_enabled": False,
                "reason": "custom_test_stub_no_send",
            }

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE_ME",
            "manual_submit_provided_token": "APPROVE_ME",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    dispatch = result.metadata["submit_dispatch"]
    assert dispatch["attempted"] is True
    assert dispatch["live_send_enabled"] is True
    assert dispatch["reason"] == "would_send_network_gated"
    assert dispatch["request_stub"]["mode"] == "submit_request_stub"
    assert dispatch["request_stub"]["send_enabled"] is False
    assert dispatch["live_send_network_enabled"] is False
    assert dispatch["would_send"]["rpc_method"] == "sendTransaction"
    assert "unsigned_submit_stub" in dispatch
    assert "signed_submit" in dispatch
    assert dispatch["signed_submit_source"] in {"", "submit_signer", "dex_executor_stub"}


def test_live_execution_adapter_manual_submit_dispatch_can_submit_via_rpc_stub_when_network_gate_enabled():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            assert transaction_base64 == "BASE64_SIGNED_TX"
            assert kwargs["skip_preflight"] is True
            assert kwargs["max_retries"] == 1
            assert kwargs["preflight_commitment"] == "confirmed"
            return "SIG_SUBMITTED_1"

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": 10_000_000, "route_count": 1}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {"mode": "submit_request_stub", "client_order_id": client_order_id}
        def build_signed_submit_stub(self, order_preview, client_order_id):
            return {
                "mode": "signed_submit_stub",
                "client_order_id": client_order_id,
                "transaction_base64": "BASE64_SIGNED_TX",
                "ready": True,
            }

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE_ME",
            "manual_submit_provided_token": "APPROVE_ME",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
            "live_send_network_enabled": True,
            "live_send_skip_preflight": True,
            "live_send_max_retries": 1,
            "live_send_preflight_commitment": "confirmed",
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    dispatch = result.metadata["submit_dispatch"]
    assert dispatch["reason"] == "send_raw_transaction_submitted"
    assert dispatch["ready"] is True
    assert dispatch["submitted_signature"] == "SIG_SUBMITTED_1"
    assert dispatch["signed_submit"]["transaction_base64"] == "BASE64_SIGNED_TX"
    assert dispatch["signed_submit_source"] == "dex_executor_stub"
    assert "unsigned_submit_stub" in dispatch
    assert dispatch["runtime_counters"]["submit_dispatch_submitted"] == 1
    assert dispatch["runtime_counters"]["session_orders_submitted"] == 1


def test_live_execution_adapter_manual_submit_dispatch_network_enabled_requires_signed_tx():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            raise AssertionError("should not be called when signed tx is missing")

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy"}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {"mode": "submit_request_stub", "client_order_id": client_order_id}
        def build_signed_submit_stub(self, order_preview, client_order_id):
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": None}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE_ME",
            "manual_submit_provided_token": "APPROVE_ME",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
            "live_send_network_enabled": True,
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    dispatch = result.metadata["submit_dispatch"]
    assert dispatch["reason"] == "missing_signed_transaction_base64"
    assert dispatch["ready"] is False


def test_live_execution_adapter_manual_submit_dispatch_can_fetch_chain_reconciliation_after_submit():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            return "SIG_CHAIN_1"
        def get_signature_status(self, signature, search_transaction_history=True):
            assert signature == "SIG_CHAIN_1"
            return {"slot": 321, "confirmationStatus": "finalized", "err": None}
        def get_transaction(self, signature, **kwargs):
            assert signature == "SIG_CHAIN_1"
            return {
                "slot": 321,
                "transaction": {"signatures": [signature]},
                "meta": {
                    "fee": 5000,
                    "err": None,
                    "preBalances": [10000],
                    "postBalances": [5000],
                    "preTokenBalances": [],
                    "postTokenBalances": [
                        {
                            "accountIndex": 0,
                            "mint": "MINT_A",
                            "owner": "OWNER1",
                            "uiTokenAmount": {"amount": "99"},
                        }
                    ],
                },
            }

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": 10_000_000, "route_count": 1}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {"mode": "submit_request_stub", "client_order_id": client_order_id}
        def build_signed_submit_stub(self, order_preview, client_order_id):
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": "BASE64_TX"}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE_ME",
            "manual_submit_provided_token": "APPROVE_ME",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
            "live_send_network_enabled": True,
            "live_send_fetch_chain_reconciliation": True,
            "live_send_chain_owner_filter": "OWNER1",
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    dispatch = result.metadata["submit_dispatch"]
    assert dispatch["reason"] == "send_raw_transaction_submitted"
    assert dispatch["chain_reconciliation_fetch"]["enabled"] is True
    assert dispatch["chain_reconciliation_fetch"]["status_fetched"] is True
    assert dispatch["chain_reconciliation_fetch"]["tx_fetched"] is True
    assert dispatch["chain_reconciliation_fetch"]["error"] == ""
    assert dispatch["chain_reconciliation"]["outcome_class"] == "live_confirmed_reconciled"
    assert dispatch["chain_reconciliation"]["terminal_reason"] == "finalized"


def test_live_execution_adapter_real_submit_branch_prefers_injected_submit_signer():
    class SubmitSignerStub:
        def __init__(self):
            self.last_context = None
        def build_signed_submit(self, order_preview, client_order_id, context=None):
            self.last_context = dict(context or {})
            return {
                "mode": "signed_submit_from_signer",
                "transaction_base64": "SIGNER_TX_B64",
                "ready": True,
                "context": dict(context or {}),
            }

    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            assert transaction_base64 == "SIGNER_TX_B64"
            return "SIG_SIGNER"

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": 10_000_000, "route_count": 1}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {"mode": "submit_request_stub", "client_order_id": client_order_id}
        def build_unsigned_submit_stub(self, order_preview, client_order_id):
            return {"mode": "unsigned_submit_stub", "client_order_id": client_order_id, "ready": True, "unsigned_transaction_base64": "UNSIGNED_TX_B64"}
        def build_signed_submit_stub(self, order_preview, client_order_id):
            raise AssertionError("dex signed stub should not be used when submit signer is provided")

    signer = SubmitSignerStub()
    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE_ME",
            "manual_submit_provided_token": "APPROVE_ME",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
            "live_send_network_enabled": True,
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
        submit_signer=signer,
    )

    result = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    dispatch = result.metadata["submit_dispatch"]
    assert dispatch["reason"] == "send_raw_transaction_submitted"
    assert dispatch["submitted_signature"] == "SIG_SIGNER"
    assert dispatch["signed_submit_source"] == "submit_signer"
    assert dispatch["signed_submit"]["transaction_base64"] == "SIGNER_TX_B64"
    assert "unsigned_submit_stub" in dispatch
    assert isinstance(signer.last_context, dict)
    assert "unsigned_submit" in signer.last_context
    assert signer.last_context["unsigned_submit"]["mode"] == "unsigned_submit_stub"


def test_live_execution_adapter_can_build_static_submit_signer_from_config():
    def rpc_transport(url, payload, timeout_seconds):
        if payload["method"] == "getHealth":
            return {"jsonrpc": "2.0", "result": "ok"}
        if payload["method"] == "getLatestBlockhash":
            return {"jsonrpc": "2.0", "result": {"value": {"blockhash": "XYZ"}}}
        return {"jsonrpc": "2.0", "result": None}

    def dex_transport(url, params, timeout_seconds):
        return {"outAmount": "999", "routePlan": [{"leg": 1}]}

    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "rpc_read_url": "https://rpc-read.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "dex_quote_url": "https://quote.example",
            "use_real_quote_clients": True,
            "dex_quote_only_mode": True,
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "submit_signer_static_tx_base64": "STATIC_SIGNER_TX",
        },
        rpc_transport=rpc_transport,
        dex_quote_transport=dex_transport,
    )
    out = adapter.submit_signer.build_signed_submit({"action": "buy"}, "coid_test")
    assert out["ready"] is True
    assert out["transaction_base64"] == "STATIC_SIGNER_TX"


def test_live_execution_adapter_can_build_command_submit_signer_from_config():
    adapter = LiveExecutionAdapter(
        {
            "live_enabled": True,
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 100,
            "submit_signer_command": ["signer.exe", "--json"],
            "submit_signer_command_timeout_seconds": 7,
        }
    )
    assert adapter.submit_signer is not None
    assert adapter.submit_signer.__class__.__name__ == "CommandSubmitSigner"


def test_live_execution_adapter_real_submit_branch_enforces_session_order_cap():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            return "SIG_OK"

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": int(usd_size * 1_000_000), "route_count": 1}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {"mode": "submit_request_stub", "client_order_id": client_order_id}
        def build_signed_submit_stub(self, order_preview, client_order_id):
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": "BASE64_TX"}

    cfg = {
        "live_enabled": True,
        "rpc_url": "https://rpc.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "allowlist_tokens": ["TOKEN_A"],
        "max_order_usd_cap": 100,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE_ME",
        "manual_submit_provided_token": "APPROVE_ME",
        "manual_submit_mode": "buy_only",
        "live_send_enabled": True,
        "live_send_network_enabled": True,
        "live_send_max_orders_per_session": 1,
    }
    adapter = LiveExecutionAdapter(cfg, rpc_client=RpcStub(), dex_executor=DexStub())

    first = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    second = adapter.buy("TOKEN_A", "SYM", 0.01, 10)

    assert first.metadata["submit_dispatch"]["reason"] == "send_raw_transaction_submitted"
    assert first.metadata["submit_dispatch"]["session_caps"]["orders_submitted_after"] == 1
    assert second.metadata["submit_dispatch"]["reason"] == "live_send_session_order_cap_reached"
    assert second.metadata["submit_dispatch"]["session_caps"]["orders_submitted"] == 1


def test_live_execution_adapter_real_submit_branch_enforces_session_notional_cap():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            return "SIG_OK"

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": int(usd_size * 1_000_000), "route_count": 1}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {"mode": "submit_request_stub", "client_order_id": client_order_id}
        def build_signed_submit_stub(self, order_preview, client_order_id):
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": "BASE64_TX"}

    cfg = {
        "live_enabled": True,
        "rpc_url": "https://rpc.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "allowlist_tokens": ["TOKEN_A"],
        "max_order_usd_cap": 100,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE_ME",
        "manual_submit_provided_token": "APPROVE_ME",
        "manual_submit_mode": "buy_only",
        "live_send_enabled": True,
        "live_send_network_enabled": True,
        "live_send_max_notional_usd_total": 15,
    }
    adapter = LiveExecutionAdapter(cfg, rpc_client=RpcStub(), dex_executor=DexStub())

    first = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    second = adapter.buy("TOKEN_A", "SYM", 0.01, 10)

    assert first.metadata["submit_dispatch"]["reason"] == "send_raw_transaction_submitted"
    assert first.metadata["submit_dispatch"]["session_caps"]["notional_usd_submitted_after"] == 10.0
    assert second.metadata["submit_dispatch"]["reason"] == "live_send_session_notional_cap_exceeded"
    assert second.metadata["submit_dispatch"]["session_caps"]["requested_notional_usd"] == 10.0


def test_live_execution_adapter_real_submit_branch_latches_pause_on_inconclusive_reconciliation():
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            return "SIG_INC"
        def get_signature_status(self, signature, search_transaction_history=True):
            return None
        def get_transaction(self, signature, **kwargs):
            return None

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": int(usd_size * 1_000_000), "route_count": 1}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {"mode": "submit_request_stub", "client_order_id": client_order_id}
        def build_signed_submit_stub(self, order_preview, client_order_id):
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": "BASE64_TX"}

    cfg = {
        "live_enabled": True,
        "rpc_url": "https://rpc.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "allowlist_tokens": ["TOKEN_A"],
        "max_order_usd_cap": 100,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE_ME",
        "manual_submit_provided_token": "APPROVE_ME",
        "manual_submit_mode": "buy_only",
        "live_send_enabled": True,
        "live_send_network_enabled": True,
        "live_send_fetch_chain_reconciliation": True,
    }
    adapter = LiveExecutionAdapter(cfg, rpc_client=RpcStub(), dex_executor=DexStub())

    first = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    second = adapter.buy("TOKEN_A", "SYM", 0.01, 10)

    first_dispatch = first.metadata["submit_dispatch"]
    assert first_dispatch["reason"] == "send_raw_transaction_submitted"
    assert first_dispatch["chain_reconciliation"]["outcome_class"] == "live_confirmation_inconclusive"
    assert first_dispatch["pause_latch"]["latched"] is True
    assert first_dispatch["pause_latch"]["reason"] == "live_confirmation_inconclusive"

    second_dispatch = second.metadata["submit_dispatch"]
    assert second_dispatch["reason"] == "live_send_paused_latched"
    assert second_dispatch["pause_latched"] is True
    assert second_dispatch["pause_reason"] == "live_confirmation_inconclusive"


def test_live_execution_adapter_real_submit_pause_latch_can_reset_with_token():
    class RpcStub:
        def __init__(self):
            self.calls = 0
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            self.calls += 1
            return f"SIG_{self.calls}"
        def get_signature_status(self, signature, search_transaction_history=True):
            if self.calls == 1:
                return None
            return {"slot": 1, "confirmationStatus": "finalized", "err": None}
        def get_transaction(self, signature, **kwargs):
            if self.calls == 1:
                return None
            return {
                "slot": 1,
                "transaction": {"signatures": [signature]},
                "meta": {
                    "fee": 5000,
                    "err": None,
                    "preBalances": [10000],
                    "postBalances": [5000],
                    "preTokenBalances": [],
                    "postTokenBalances": [{"accountIndex": 0, "mint": "MINT_A", "owner": "OWNER1", "uiTokenAmount": {"amount": "10"}}],
                },
            }

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"amount": int(usd_size * 1_000_000), "route_count": 1}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {"mode": "submit_request_stub", "client_order_id": client_order_id}
        def build_signed_submit_stub(self, order_preview, client_order_id):
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": "BASE64_TX"}

    cfg = {
        "live_enabled": True,
        "rpc_url": "https://rpc.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "allowlist_tokens": ["TOKEN_A"],
        "max_order_usd_cap": 100,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE_ME",
        "manual_submit_provided_token": "APPROVE_ME",
        "manual_submit_mode": "buy_only",
        "live_send_enabled": True,
        "live_send_network_enabled": True,
        "live_send_fetch_chain_reconciliation": True,
        "live_send_pause_reset_required_token": "RESET_ME",
    }
    adapter = LiveExecutionAdapter(cfg, rpc_client=RpcStub(), dex_executor=DexStub())

    first = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    blocked = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    assert first.metadata["submit_dispatch"]["pause_latch"]["latched"] is True
    assert blocked.metadata["submit_dispatch"]["reason"] == "live_send_paused_latched"

    adapter.config["live_send_pause_reset_provided_token"] = "RESET_ME"
    reset_attempt = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    dispatch = reset_attempt.metadata["submit_dispatch"]
    assert dispatch["pause_reset"]["reset_applied"] is True
    assert dispatch["reason"] == "send_raw_transaction_submitted"
    assert dispatch["runtime_counters"]["submit_dispatch_pause_reset_events"] == 1


def test_live_execution_adapter_allows_sell_submit_when_mode_is_buy_and_sell():
    class RpcStub:
        def __init__(self):
            self.calls = 0
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            self.calls += 1
            return f"SIG_{self.calls}"

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {"action": "buy", "quote_preview": {"out_amount": "12345", "amount": int(usd_size * 1_000_000), "route_count": 1}}
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell", "quote_preview": {"amount": 12345, "route_count": 1}}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss", "quote_preview": {"amount": 12345, "route_count": 1}}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {"mode": "submit_request_stub", "client_order_id": client_order_id}
        def build_signed_submit_stub(self, order_preview, client_order_id):
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": "BASE64_TX"}

    cfg = {
        "live_enabled": True,
        "rpc_url": "https://rpc.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "allowlist_tokens": ["TOKEN_A"],
        "max_order_usd_cap": 100,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE_ME",
        "manual_submit_provided_token": "APPROVE_ME",
        "manual_submit_mode": "buy_and_sell",
        "live_send_enabled": True,
        "live_send_network_enabled": True,
    }
    adapter = LiveExecutionAdapter(cfg, rpc_client=RpcStub(), dex_executor=DexStub())

    buy = adapter.buy("TOKEN_A", "SYM", 0.01, 10)
    sell = adapter.sell(int(buy.position_id), 0.02)

    assert buy.metadata["submit_dispatch"]["reason"] == "send_raw_transaction_submitted"
    assert sell.metadata["submit_dispatch"]["reason"] == "send_raw_transaction_submitted"

