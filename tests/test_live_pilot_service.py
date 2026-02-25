import json

from src.live.live_pilot_service import run_live_pilot_service_loop, run_live_pilot_service_once


def test_run_live_pilot_service_once_emits_rollup_and_audit(tmp_path):
    adapter_cfg = {
        "rpc_url": "https://rpc.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "allowlist_tokens": ["TOKEN_A"],
        "max_order_usd_cap": 100,
        "pilot_hard_max_order_usd_cap": 200,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE",
        "manual_submit_provided_token": "APPROVE",
        "manual_submit_mode": "buy_only",
        "live_send_enabled": True,
        # network gate off keeps this safe and deterministic in service test
        "live_send_network_enabled": False,
    }
    out = run_live_pilot_service_once(
        token_address="TOKEN_A",
        symbol="TKA",
        entry_price=0.01,
        usd_size=10,
        audit_log_dir=str(tmp_path),
        adapter_config=adapter_cfg,
    )
    assert out["rollup"]["runs"] == 1
    assert out["rollup"]["submit_dispatch_by_reason"]["would_send_network_gated"] == 1
    assert out["rollup"]["submitted_signatures"] == 0

    jsonl = next(p for p in tmp_path.iterdir() if p.suffix == ".jsonl")
    lines = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    event_types = [row["event_type"] for row in lines]
    assert "live_pilot_service_started" in event_types
    assert "live_pilot_execution_result" in event_types
    assert "live_manual_submit_gate" in event_types
    assert "live_submit_dispatch" in event_types
    assert "live_pilot_service_completed" in event_types


def test_run_live_pilot_service_loop_rolls_up_multiple_cycles(tmp_path):
    adapter_cfg = {
        "rpc_url": "https://rpc.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "allowlist_tokens": ["TOKEN_A"],
        "max_order_usd_cap": 100,
        "pilot_hard_max_order_usd_cap": 200,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE",
        "manual_submit_provided_token": "APPROVE",
        "manual_submit_mode": "buy_only",
        "live_send_enabled": True,
        "live_send_network_enabled": False,
    }
    out = run_live_pilot_service_loop(
        token_address="TOKEN_A",
        symbol="TKA",
        entry_price=0.01,
        usd_size=10,
        iterations=3,
        audit_log_dir=str(tmp_path),
        adapter_config=adapter_cfg,
    )
    assert out["rollup"]["runs"] == 3
    assert out["rollup"]["cycles_completed"] == 3
    assert out["rollup"]["submit_dispatch_by_reason"]["would_send_network_gated"] == 3
    assert len(out["cycles"]) == 3


def test_run_live_pilot_service_loop_reuses_adapter_state_for_caps(tmp_path):
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

    adapter_cfg = {
        "rpc_url": "https://rpc.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "allowlist_tokens": ["TOKEN_A"],
        "max_order_usd_cap": 100,
        "pilot_hard_max_order_usd_cap": 200,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE",
        "manual_submit_provided_token": "APPROVE",
        "manual_submit_mode": "buy_only",
        "live_send_enabled": True,
        "live_send_network_enabled": True,
        "live_send_max_orders_per_session": 1,
    }

    out = run_live_pilot_service_loop(
        token_address="TOKEN_A",
        symbol="TKA",
        entry_price=0.01,
        usd_size=10,
        iterations=2,
        audit_log_dir=str(tmp_path),
        adapter_config=adapter_cfg,
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )
    assert out["rollup"]["runs"] == 2
    assert out["rollup"]["submit_dispatch_by_reason"]["send_raw_transaction_submitted"] == 1
    assert out["rollup"]["submit_dispatch_by_reason"]["live_send_session_order_cap_reached"] == 1


def test_run_live_pilot_service_once_command_signer_with_mocked_swap_transport_builds_unsigned_no_send(tmp_path):
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "XYZ"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}

    def dex_quote_transport(url, params, timeout_seconds):
        return {"outAmount": "111", "routePlan": [{"leg": 1}], "quoteId": "q1"}

    def dex_swap_transport(url, body, timeout_seconds):
        return {"swapTransaction": "UNSIGNED_TX_B64"}

    adapter_cfg = {
        "rpc_url": "https://rpc.example",
        "rpc_read_url": "https://rpc-read.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "use_real_quote_clients": True,
        "dex_quote_url": "https://quote.example",
        "dex_swap_url": "https://swap.example",
        "dex_quote_only_mode": True,
        "allowlist_tokens": ["TOKEN_A"],
        "max_order_usd_cap": 100,
        "pilot_hard_max_order_usd_cap": 200,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE",
        "manual_submit_provided_token": "APPROVE",
        "manual_submit_mode": "buy_only",
        "live_send_enabled": True,
        "live_send_network_enabled": True,
        "submit_signer_command": ["python", "examples/signer_demo.py"],
    }
    out = run_live_pilot_service_once(
        token_address="TOKEN_A",
        symbol="TKA",
        entry_price=0.01,
        usd_size=10,
        audit_log_dir=str(tmp_path),
        adapter_config=adapter_cfg,
        rpc_client=RpcStub(),
        dex_quote_transport=dex_quote_transport,
        dex_swap_transport=dex_swap_transport,
    )
    assert out["rollup"]["submit_dispatch_by_reason"]["rpc_send_raw_transaction_not_supported"] == 1

    jsonl = next(p for p in tmp_path.iterdir() if p.suffix == ".jsonl")
    lines = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    dispatch = next(row["payload"] for row in lines if row["event_type"] == "live_submit_dispatch")
    assert dispatch["reason"] == "rpc_send_raw_transaction_not_supported"
    assert dispatch["signed_submit_source"] == "submit_signer"
    assert dispatch["unsigned_submit_stub"]["ready"] is True
    assert dispatch["unsigned_submit_stub"]["unsigned_transaction_base64"] == "UNSIGNED_TX_B64"
    signer_meta = (((dispatch.get("signed_submit") or {}).get("signer_response") or {}).get("meta") or {})
    assert signer_meta.get("unsigned_submit_ready") is True
    assert signer_meta.get("used_unsigned_submit_passthrough") is True
