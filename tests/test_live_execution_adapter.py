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
    assert sell.metadata["estimated_costs"]["ok"] is False
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
