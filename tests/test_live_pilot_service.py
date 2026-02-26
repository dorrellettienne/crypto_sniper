import json

from src.live.live_pilot_service import (
    aggregate_live_pilot_campaign_reports,
    adaptive_reorder_fallback_candidates,
    adaptive_reorder_provider_order,
    _apply_live_pilot_mode_preset,
    _evaluate_live_pilot_promotion_gates,
    _extract_live_submit_economics,
    _build_live_pilot_mechanical_safety_filter_from_config,
    _build_live_pilot_preflight,
    _build_live_pilot_signal_provider_from_args,
    _build_live_pilot_volatility_guard_from_config,
    _format_human_live_pilot_summary,
    _extract_campaign_report_summary,
    _evaluate_live_pilot_promotion_gates,
    load_adaptive_reliability_state,
    _validate_live_auto_window_guardrails,
    probe_fallback_candidates_preflight,
    run_live_pilot_auto_window,
    run_live_pilot_auto_window_candidates,
    run_live_pilot_auto_window_from_signal_provider,
    run_live_pilot_campaign,
    run_live_pilot_campaign_schedule,
    sanitize_fallback_candidates,
    save_adaptive_reliability_state,
    update_adaptive_reliability_state_from_campaign_report,
    build_live_pilot_daily_operator_report,
    append_live_pilot_operator_decision_log,
    apply_operator_acknowledgement_to_daily_report,
    _build_campaign_alert_emitter,
    build_live_pilot_artifact_index,
    write_live_pilot_artifact_index,
    build_live_pilot_handoff_snapshot,
    write_live_pilot_handoff_snapshot,
    validate_live_pilot_campaign_state,
    validate_live_pilot_schedule_state,
    build_live_pilot_run_manifest,
    write_live_pilot_run_manifest,
    verify_live_pilot_validation_bundle,
    write_live_pilot_bundle_verification,
    build_live_pilot_session_timeline,
    write_live_pilot_session_timeline,
    get_live_pilot_risk_profile_preset,
    build_live_pilot_promotion_step_manifest,
    write_live_pilot_promotion_step_manifest,
    build_live_pilot_prelive_go_no_go_checklist,
    write_live_pilot_prelive_go_no_go_checklist,
    write_live_pilot_daily_operator_report,
    write_campaign_trend_report,
    run_live_pilot_service_loop,
    run_live_pilot_service_once,
)


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


def test_run_live_pilot_service_once_auto_attaches_chain_reconciliation_and_summary(tmp_path):
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            return "SIG_FINAL_1"
        def get_signature_status(self, signature, search_transaction_history=True):
            assert signature == "SIG_FINAL_1"
            return {"slot": 321, "confirmationStatus": "finalized", "err": None}
        def get_transaction(self, signature, **kwargs):
            assert signature == "SIG_FINAL_1"
            return {
                "slot": 321,
                "transaction": {"signatures": [signature]},
                "meta": {
                    "fee": 5000,
                    "err": None,
                    "preBalances": [10_000],
                    "postBalances": [5_000],
                    "preTokenBalances": [],
                    "postTokenBalances": [
                        {
                            "mint": "TOKEN_A",
                            "owner": "wallet_pub",
                            "accountIndex": 0,
                            "uiTokenAmount": {"amount": "100"},
                        }
                    ],
                },
            }

    class DexStub:
        def build_buy_order(self, token_address, symbol, entry_price, usd_size):
            return {
                "action": "buy",
                "quote_preview": {
                    "amount": int(usd_size * 1_000_000),
                    "route_count": 1,
                    "out_amount": "110",
                    "raw_quote": {"outAmount": "110"},
                },
            }
        def build_sell_order(self, position_id, exit_price):
            return {"action": "sell"}
        def build_stop_loss_order(self, position_id, stop_percent):
            return {"action": "stop_loss"}
        def build_submit_preview(self, order_preview, client_order_id):
            return {"mode": "submit_skeleton", "client_order_id": client_order_id}
        def build_submit_request_stub(self, order_preview, client_order_id):
            return {"mode": "submit_request_stub", "client_order_id": client_order_id}
        def build_unsigned_submit_stub(self, order_preview, client_order_id):
            return {"mode": "unsigned_submit_stub", "client_order_id": client_order_id, "ready": True, "unsigned_transaction_base64": "UNSIGNED_TX"}
        def build_signed_submit_stub(self, order_preview, client_order_id):
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": "SIGNED_TX", "ready": True}

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
    }
    out = run_live_pilot_service_once(
        token_address="TOKEN_A",
        symbol="TKA",
        entry_price=0.01,
        usd_size=1,
        audit_log_dir=str(tmp_path),
        adapter_config=adapter_cfg,
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
    )
    dispatch = ((out["result"].get("metadata") or {}).get("submit_dispatch") or {})
    assert dispatch["reason"] == "send_raw_transaction_submitted"
    assert dispatch["submitted_signature"] == "SIG_FINAL_1"
    assert dispatch["chain_reconciliation"]["outcome_class"] == "live_confirmed_reconciled"
    assert dispatch["chain_reconciliation"]["terminal_reason"] == "finalized"
    assert out["rollup"]["live_finalized_count"] == 1
    assert out["rollup"]["live_reconciliation_outcome_by_class"]["live_confirmed_reconciled"] == 1
    assert out["rollup"]["economics_samples_count"] == 1
    assert out["rollup"]["fee_lamports_total"] == 5000
    assert out["rollup"]["avg_realized_slippage_bps"] > 0
    assert out["rollup"]["quote_vs_settlement_mismatch_count"] == 1
    assert out["live_pilot_summary"]["submit_reason"] == "send_raw_transaction_submitted"
    assert out["live_pilot_summary"]["submitted_signature"] == "SIG_FINAL_1"
    assert out["live_pilot_summary"]["chain_outcome_class"] == "live_confirmed_reconciled"
    assert out["live_pilot_summary"]["economics"]["quote_expected_out_amount_raw"] == 110
    assert out["live_pilot_summary"]["economics"]["settlement_actual_out_amount_raw"] == 100
    assert out["promotion_gate_summary"]["status"] == "fail"
    assert "min_finalized_pilots" in out["promotion_gate_summary"]["failed_checks"]

    jsonl = next(p for p in tmp_path.iterdir() if p.suffix == ".jsonl")
    lines = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    event_types = [row["event_type"] for row in lines]
    assert "live_submit_chain_reconciliation" in event_types
    assert "live_pilot_service_summary" in event_types
    assert "live_pilot_promotion_gate_evaluation" in event_types


def test_extract_live_submit_economics_computes_quote_vs_settlement_slippage():
    payload = {
        "metadata": {
            "token_address": "TOKEN_A",
            "symbol": "TKA",
            "usd_size": 1.0,
            "estimated_costs": {
                "notional_usd": 1.0,
                "estimated_total_cost_usd": 0.108,
                "estimated_slippage_usd": 0.005,
                "network_fee_usd": 0.1,
                "slippage_bps_effective": 50.0,
                "out_amount": "1000",
            },
            "submit_dispatch": {
                "submitted_signature": "SIG1",
                "chain_reconciliation": {
                    "outcome_class": "live_confirmed_reconciled",
                    "settlement_summary": {
                        "fee_lamports": 5000,
                        "token_deltas_by_mint": {"TOKEN_A": 950},
                    },
                },
            },
        }
    }
    econ = _extract_live_submit_economics(payload, thresholds={"max_quote_to_settlement_diff_bps": 100, "usd_to_lamports": 10_000_000})
    assert econ["quote_expected_out_amount_raw"] == 1000
    assert econ["settlement_actual_out_amount_raw"] == 950
    assert econ["quote_vs_settlement_out_diff_raw"] == -50
    assert econ["realized_slippage_bps_vs_quote"] == 500.0
    assert econ["quote_vs_settlement_mismatch"] is True
    assert econ["fee_usd_realized_proxy"] == 0.0005


def test_evaluate_live_pilot_promotion_gates_pass_and_fail():
    fail = _evaluate_live_pilot_promotion_gates(
        {
            "live_finalized_count": 1,
            "live_reconciliation_mismatch_count": 1,
            "pause_latch_events": 0,
            "economics_samples_count": 1,
            "quote_vs_settlement_mismatch_count": 1,
            "worst_realized_slippage_bps": 300.0,
            "signal_provider_metrics": {"fetch_transport_errors": 1, "fetch_endpoint_failure_events": 2},
        }
    )
    assert fail["status"] == "fail"
    assert "min_finalized_pilots" in fail["failed_checks"]
    assert "max_worst_realized_slippage_bps" in fail["failed_checks"]

    ok = _evaluate_live_pilot_promotion_gates(
        {
            "live_finalized_count": 3,
            "live_reconciliation_mismatch_count": 0,
            "pause_latch_events": 0,
            "economics_samples_count": 3,
            "quote_vs_settlement_mismatch_count": 0,
            "worst_realized_slippage_bps": 40.0,
            "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
        }
    )
    assert ok["status"] == "pass"
    assert ok["ready_to_promote"] is True


def test_run_live_pilot_auto_window_stops_after_max_auto_trades(tmp_path):
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            return "SIG_AUTO_1"
        def get_signature_status(self, signature, search_transaction_history=True):
            return {"slot": 1, "confirmationStatus": "finalized", "err": None}
        def get_transaction(self, signature, **kwargs):
            return {
                "slot": 1,
                "transaction": {"signatures": [signature]},
                "meta": {"fee": 5000, "err": None, "preBalances": [100], "postBalances": [50], "preTokenBalances": [], "postTokenBalances": []},
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
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": "SIGNED_TX", "ready": True}

    t = {"now": 0.0}
    def now_fn():
        return t["now"]
    def sleep_fn(seconds):
        t["now"] += float(seconds)

    out = run_live_pilot_auto_window(
        token_address="TOKEN_A",
        symbol="TKA",
        entry_price=0.01,
        usd_size=1,
        window_seconds=60,
        max_auto_trades=1,
        poll_interval_seconds=0,
        stop_on_reconciliation_mismatch=False,
        audit_log_dir=str(tmp_path),
        adapter_config={
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 10,
            "pilot_hard_max_order_usd_cap": 10,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE",
            "manual_submit_provided_token": "APPROVE",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
            "live_send_network_enabled": True,
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
        now_fn=now_fn,
        sleep_fn=sleep_fn,
    )
    assert out["rollup"]["auto_window"]["trades_submitted"] == 1
    assert out["rollup"]["auto_window"]["stop_reason"] == "max_auto_trades_reached"
    assert out["rollup"]["submitted_signatures"] == 1
    assert out["rollup"]["live_finalized_count"] == 1


def test_run_live_pilot_auto_window_can_stop_on_reconciliation_inconclusive(tmp_path):
    class RpcStub:
        def health_check(self):
            return {"ok": True, "client": "rpc_stub"}
        def get_recent_blockhash(self):
            return "STUB_HASH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            return "SIG_AUTO_2"
        def get_signature_status(self, signature, search_transaction_history=True):
            return {"slot": 2, "confirmationStatus": "processed", "err": None}
        def get_transaction(self, signature, **kwargs):
            return {"slot": 2, "transaction": {"signatures": [signature]}, "meta": {"fee": 5000, "err": None, "preBalances": [100], "postBalances": [50], "preTokenBalances": [], "postTokenBalances": []}}

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
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": "SIGNED_TX", "ready": True}

    t = {"now": 0.0}
    out = run_live_pilot_auto_window(
        token_address="TOKEN_A",
        symbol="TKA",
        entry_price=0.01,
        usd_size=1,
        window_seconds=60,
        max_auto_trades=5,
        poll_interval_seconds=0,
        stop_on_reconciliation_inconclusive=True,
        audit_log_dir=str(tmp_path),
        adapter_config={
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A"],
            "max_order_usd_cap": 10,
            "pilot_hard_max_order_usd_cap": 10,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE",
            "manual_submit_provided_token": "APPROVE",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
            "live_send_network_enabled": True,
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
        now_fn=lambda: t["now"],
        sleep_fn=lambda s: t.__setitem__("now", t["now"] + float(s)),
    )
    assert out["rollup"]["auto_window"]["trades_submitted"] == 1
    assert out["rollup"]["auto_window"]["stop_reason"] == "reconciliation_inconclusive"
    assert out["rollup"]["live_reconciliation_outcome_by_class"]["live_confirmation_inconclusive"] == 1


def test_run_live_pilot_auto_window_candidates_stops_after_one_submitted_trade(tmp_path):
    class RpcStub:
        def health_check(self):
            return {"ok": True}
        def get_recent_blockhash(self):
            return "BH"
        def build_confirm_preview(self, client_order_id):
            return {"mode": "confirm_skeleton", "client_order_id": client_order_id}
        def send_raw_transaction(self, transaction_base64, **kwargs):
            return "SIG_CAND_1"
        def get_signature_status(self, signature, search_transaction_history=True):
            return {"slot": 10, "confirmationStatus": "finalized", "err": None}
        def get_transaction(self, signature, **kwargs):
            return {
                "slot": 10,
                "transaction": {"signatures": [signature]},
                "meta": {"fee": 5000, "err": None, "preBalances": [100], "postBalances": [50], "preTokenBalances": [], "postTokenBalances": []},
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
            return {"mode": "signed_submit_stub", "client_order_id": client_order_id, "transaction_base64": "SIGNED_TX", "ready": True}

    t = {"now": 0.0}
    out = run_live_pilot_auto_window_candidates(
        candidates=[
            {"token_address": "TOKEN_A", "symbol": "TKA", "entry_price": 0.01, "usd_size": 1},
            {"token_address": "TOKEN_B", "symbol": "TKB", "entry_price": 0.02, "usd_size": 1},
        ],
        window_seconds=60,
        max_auto_trades=1,
        stop_on_reconciliation_mismatch=False,
        audit_log_dir=str(tmp_path),
        adapter_config={
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_A", "TOKEN_B"],
            "max_order_usd_cap": 10,
            "pilot_hard_max_order_usd_cap": 10,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE",
            "manual_submit_provided_token": "APPROVE",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
            "live_send_network_enabled": True,
        },
        rpc_client=RpcStub(),
        dex_executor=DexStub(),
        now_fn=lambda: t["now"],
        sleep_fn=lambda s: t.__setitem__("now", t["now"] + float(s)),
    )
    assert out["rollup"]["auto_window"]["stop_reason"] == "max_auto_trades_reached"
    assert out["rollup"]["candidates_seen"] == 1
    assert out["rollup"]["candidates_attempted"] == 1
    assert out["rollup"]["candidates_submitted"] == 1
    assert out["rollup"]["submitted_signatures"] == 1
    assert out["rollup"]["candidate_submit_reason_by_reason"]["send_raw_transaction_submitted"] == 1
    assert len(out["cycles"]) == 1


def test_run_live_pilot_auto_window_candidates_all_blocked_tracks_reasons(tmp_path):
    adapter_cfg = {
        "rpc_url": "https://rpc.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "allowlist_tokens": ["TOKEN_A"],
        "max_order_usd_cap": 10,
        "pilot_hard_max_order_usd_cap": 10,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE",
        "manual_submit_provided_token": "APPROVE",
        "manual_submit_mode": "buy_only",
        "live_send_enabled": True,
        "live_send_network_enabled": False,
    }
    out = run_live_pilot_auto_window_candidates(
        candidates=[
            {"symbol": "MISSING"},  # invalid candidate
            {"token_address": "TOKEN_B", "symbol": "TKB", "entry_price": 0.02, "usd_size": 1},  # allowlist blocked
            {"token_address": "TOKEN_A", "symbol": "TKA", "entry_price": 0.01, "usd_size": 1},  # no-send gate
        ],
        window_seconds=60,
        max_auto_trades=1,
        audit_log_dir=str(tmp_path),
        adapter_config=adapter_cfg,
        now_fn=lambda: 0.0,
        sleep_fn=lambda s: None,
    )
    assert out["rollup"]["auto_window"]["stop_reason"] == "candidates_exhausted"
    assert out["rollup"]["candidates_seen"] == 3
    assert out["rollup"]["candidates_attempted"] == 2
    assert out["rollup"]["candidates_submitted"] == 0
    reasons = out["rollup"]["candidate_submit_reason_by_reason"]
    assert reasons["invalid_candidate_missing_token_address"] == 1
    assert reasons["would_send_network_gated"] == 2


def test_run_live_pilot_auto_window_candidates_mechanical_and_volatility_hooks(tmp_path):
    class MechanicalBlocker:
        def assess(self, signal):
            if str(signal.token_address) == "TOKEN_BLOCK":
                class R:
                    allowed = False
                    primary_reason = "mint_authority_enabled"
                    reasons = ["mint_authority_enabled"]
                    details = {"source": "test"}
                return R()
            class R:
                allowed = True
                primary_reason = ""
                reasons = []
                details = {}
            return R()

    class VolGuard:
        def __init__(self):
            self.calls = 0
        def assess(self, *, token_address, symbol, requested_usd_size):
            self.calls += 1
            class D:
                allowed = True
                reason = "derisk_applied"
                derisk_applied = True
                adjusted_usd_size = 0.5
                details = {"requested_usd_size": requested_usd_size}
            return D()

    adapter_cfg = {
        "rpc_url": "https://rpc.example",
        "wallet_public_key": "wallet_pub",
        "dex_name": "JUPITER",
        "allowlist_tokens": ["TOKEN_BLOCK", "TOKEN_OK"],
        "max_order_usd_cap": 10,
        "pilot_hard_max_order_usd_cap": 10,
        "live_submit_skeleton_enabled": True,
        "manual_submit_approval_enabled": True,
        "manual_submit_required_token": "APPROVE",
        "manual_submit_provided_token": "APPROVE",
        "manual_submit_mode": "buy_only",
        "live_send_enabled": True,
        "live_send_network_enabled": False,
    }
    vol = VolGuard()
    out = run_live_pilot_auto_window_candidates(
        candidates=[
            {"token_address": "TOKEN_BLOCK", "symbol": "TB", "entry_price": 1, "usd_size": 1},
            {"token_address": "TOKEN_OK", "symbol": "TO", "entry_price": 1, "usd_size": 1},
        ],
        window_seconds=60,
        max_auto_trades=1,
        audit_log_dir=str(tmp_path),
        adapter_config=adapter_cfg,
        mechanical_safety_filter=MechanicalBlocker(),
        volatility_guard=vol,
        now_fn=lambda: 0.0,
        sleep_fn=lambda s: None,
    )
    assert out["rollup"]["candidates_seen"] == 2
    assert out["rollup"]["candidates_attempted"] == 2
    assert out["rollup"]["mechanical_blocked_by_reason"]["mint_authority_enabled"] == 1
    assert out["rollup"]["candidate_skip_reason_by_reason"]["mint_authority_enabled"] == 1
    assert out["rollup"]["volatility_guard_derisked_count"] == 1
    assert out["rollup"]["submit_dispatch_by_reason"]["would_send_network_gated"] == 1
    assert vol.calls == 1


def test_live_pilot_service_builds_config_based_candidate_hooks():
    mech = _build_live_pilot_mechanical_safety_filter_from_config(
        {
            "live_pilot_mechanical_safety": {
                "enabled": True,
                "require_buy_route": True,
                "require_sell_route": True,
                "max_buy_price_impact_pct": 10,
            }
        }
    )
    assert mech is not None
    desc = mech.describe()
    assert desc["require_buy_route"] is True
    assert desc["require_sell_route"] is True
    assert desc["max_buy_price_impact_pct"] == 10.0

    vg = _build_live_pilot_volatility_guard_from_config(
        {
            "live_pilot_volatility_guard": {
                "enabled": True,
                "loss_streak_derisk_threshold": 2,
                "derisk_size_multiplier": 0.5,
                "derisk_min_usd_size": 0.25,
            }
        }
    )
    assert vg is not None
    vdesc = vg.describe()
    assert vdesc["enabled"] is True
    assert vdesc["loss_streak_derisk_threshold"] == 2
    assert vdesc["derisk_size_multiplier"] == 0.5
    assert vdesc["derisk_min_usd_size"] == 0.25


def test_run_live_pilot_auto_window_from_signal_provider_maps_signals_and_rejects_stale(tmp_path):
    from src.live.interfaces import TradeSignal
    from src.live.signal_provider_stub import StubSignalProvider

    now_s = 1000.0
    provider = StubSignalProvider(
        [
            TradeSignal(
                token_address="TOKEN_STALE",
                symbol="STALE",
                entry_price=1.0,
                usd_size=1.0,
                metadata={"observed_at_unix_ms": int((now_s - 100.0) * 1000)},
            ),
            TradeSignal(
                token_address="TOKEN_OK",
                symbol="TOK",
                entry_price=1.0,
                usd_size=1.0,
                metadata={"observed_at_unix_ms": int((now_s - 1.0) * 1000)},
            ),
        ]
    )

    out = run_live_pilot_auto_window_from_signal_provider(
        signal_provider=provider,
        window_seconds=30,
        max_auto_trades=1,
        max_candidates_per_window=5,
        require_fresh_signal_seconds=10,
        audit_log_dir=str(tmp_path),
        adapter_config={
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_OK"],
            "max_order_usd_cap": 10,
            "pilot_hard_max_order_usd_cap": 10,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE",
            "manual_submit_provided_token": "APPROVE",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
            "live_send_network_enabled": False,
        },
        now_fn=lambda: now_s,
        sleep_fn=lambda s: None,
    )
    assert out["rollup"]["signals_seen"] == 2
    assert out["rollup"]["signals_accepted"] == 1
    assert out["rollup"]["signals_rejected"] == 1
    assert out["rollup"]["signal_rejected_by_reason"]["stale_signal"] == 1
    assert out["rollup"]["candidates_seen"] == 1
    assert out["rollup"]["submit_dispatch_by_reason"]["would_send_network_gated"] == 1


def test_run_live_pilot_auto_window_from_signal_provider_includes_provider_metrics(tmp_path):
    from src.live.signal_provider_dexscreener import DexScreenerSignalProvider

    state = {"n": 0}

    def fetcher():
        state["n"] += 1
        if state["n"] == 1:
            return {
                "pairs": [
                    {
                        "chainId": "solana",
                        "pairAddress": "PAIR1",
                        "priceUsd": "1.0",
                        "liquidity": {"usd": 20000},
                        "baseToken": {"address": "TOKEN_OK", "symbol": "TOK"},
                    }
                ],
                "_fetch_meta": {
                    "retry_events": 2,
                    "stale_payload": False,
                    "selected_url": "https://example.test/fallback",
                    "endpoint_attempts": [
                        {"url": "https://example.test/primary", "success": False},
                        {"url": "https://example.test/fallback", "success": True},
                    ],
                },
            }
        return {
            "pairs": [],
            "_fetch_meta": {
                "retry_events": 0,
                "stale_payload": True,
                "selected_url": "https://example.test/primary",
                "endpoint_attempts": [{"url": "https://example.test/primary", "success": True}],
            },
        }

    provider = DexScreenerSignalProvider(fetcher, default_usd_size=1, chain_id="solana", now_ts_fn=lambda: 1000.0)
    out = run_live_pilot_auto_window_from_signal_provider(
        signal_provider=provider,
        window_seconds=30,
        max_auto_trades=1,
        max_candidates_per_window=3,
        audit_log_dir=str(tmp_path),
        adapter_config={
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_OK"],
            "max_order_usd_cap": 10,
            "pilot_hard_max_order_usd_cap": 10,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE",
            "manual_submit_provided_token": "APPROVE",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
            "live_send_network_enabled": False,
        },
        now_fn=lambda: 1000.0,
        sleep_fn=lambda s: None,
    )
    pm = out["rollup"]["signal_provider_metrics"]
    assert pm["fetch_retry_events"] == 2
    assert pm["fetch_stale_payload_events"] == 1
    assert pm["fetch_fallback_selected_events"] == 1
    assert pm["fetch_endpoint_failure_events"] == 1
    assert pm["last_fetch_meta"]["selected_url"] == "https://example.test/primary"
    assert out["rollup"]["signals_seen"] == 1
    assert out["rollup"]["signals_accepted"] == 1
    assert out["rollup"]["submit_dispatch_by_reason"]["would_send_network_gated"] == 1
    jsonl = next(p for p in tmp_path.iterdir() if p.suffix == ".jsonl")
    rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    completed = [r for r in rows if r.get("event_type") == "live_pilot_service_completed"][-1]
    assert completed["payload"]["completion_stage"] == "signal_provider_enriched"
    assert completed["payload"]["rollup"]["signal_provider_metrics"]["fetch_retry_events"] == 2


def test_run_live_pilot_auto_window_from_signal_provider_captures_endpoint_failures_on_total_fetch_failure(tmp_path):
    from src.live.dexscreener_transport import DexScreenerFetchError
    from src.live.signal_provider_dexscreener import DexScreenerSignalProvider

    def fetcher():
        raise DexScreenerFetchError(
            "HTTP Error 403: Forbidden",
            fetch_meta={
                "retry_events": 0,
                "selected_url": "",
                "endpoint_attempts": [
                    {"url": "https://example.test/primary", "success": False},
                    {"url": "https://example.test/fallback1", "success": False},
                    {"url": "https://example.test/fallback2", "success": False},
                ],
            },
        )

    provider = DexScreenerSignalProvider(fetcher, default_usd_size=1, chain_id="solana", now_ts_fn=lambda: 1000.0)
    out = run_live_pilot_auto_window_from_signal_provider(
        signal_provider=provider,
        window_seconds=30,
        max_auto_trades=1,
        max_candidates_per_window=3,
        audit_log_dir=str(tmp_path),
        adapter_config={
            "rpc_url": "https://rpc.example",
            "wallet_public_key": "wallet_pub",
            "dex_name": "JUPITER",
            "allowlist_tokens": ["TOKEN_OK"],
            "max_order_usd_cap": 10,
            "pilot_hard_max_order_usd_cap": 10,
            "live_submit_skeleton_enabled": True,
            "manual_submit_approval_enabled": True,
            "manual_submit_required_token": "APPROVE",
            "manual_submit_provided_token": "APPROVE",
            "manual_submit_mode": "buy_only",
            "live_send_enabled": True,
            "live_send_network_enabled": False,
        },
        now_fn=lambda: 1000.0,
        sleep_fn=lambda s: None,
    )
    pm = out["rollup"]["signal_provider_metrics"]
    assert pm["fetch_transport_errors"] == 1
    assert pm["fetch_endpoint_failure_events"] == 3
    assert pm["fetch_fallback_selected_events"] == 0
    assert out["rollup"]["signals_seen"] == 0
    assert out["rollup"]["auto_window"]["stop_reason"] == "candidates_exhausted"


def test_build_live_pilot_signal_provider_from_args_dexscreener():
    class Args:
        signal_provider_json_path = ""
        use_dexscreener_signals = True
        dexscreener_fetch_url = ""
        dexscreener_fetch_timeout_seconds = 5.0
        dexscreener_fetch_max_attempts = 1
        dexscreener_fetch_retry_backoff_seconds = 0.0
        dexscreener_max_payload_age_ms = None
        dexscreener_allow_stale_payloads = False
        dexscreener_chain_id = "solana"
        dexscreener_min_liquidity_usd = 1000.0
        dexscreener_max_pair_age_seconds = 300.0
        usd_size = 1.0

    provider = _build_live_pilot_signal_provider_from_args(Args())
    assert provider is not None
    # Empty default fetcher path yields no signals but valid provider object.
    assert provider.get_next_signal() is None


def test_build_live_pilot_signal_provider_from_args_dexscreener_passes_headers_and_fallbacks(tmp_path, monkeypatch):
    fallback_path = tmp_path / "fallbacks.json"
    fallback_path.write_text(json.dumps(["https://example.test/fallback1", "https://example.test/fallback2"]), encoding="utf-8-sig")
    captured = {}

    class FakeFetcher:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def __call__(self):
            return {"pairs": []}

    monkeypatch.setattr("src.live.live_pilot_service.DexScreenerHttpPairsFetcher", FakeFetcher)

    class Args:
        signal_provider_json_path = ""
        use_dexscreener_signals = True
        dexscreener_fetch_url = "https://example.test/primary"
        dexscreener_fallback_urls_json_path = str(fallback_path)
        dexscreener_fetch_timeout_seconds = 5.0
        dexscreener_fetch_max_attempts = 2
        dexscreener_fetch_retry_backoff_seconds = 0.1
        dexscreener_max_payload_age_ms = 1000
        dexscreener_allow_stale_payloads = False
        dexscreener_user_agent = "crypto-sniper-test/1.0"
        dexscreener_header = ["Accept: application/json", "X-Test: yes"]
        dexscreener_chain_id = "solana"
        dexscreener_min_liquidity_usd = None
        dexscreener_max_pair_age_seconds = None
        usd_size = 1.0

    provider = _build_live_pilot_signal_provider_from_args(Args())
    assert provider is not None
    assert captured["url"] == "https://example.test/primary"
    assert captured["fallback_urls"] == ["https://example.test/fallback1", "https://example.test/fallback2"]
    assert captured["headers"]["User-Agent"] == "crypto-sniper-test/1.0"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["headers"]["X-Test"] == "yes"


def test_build_live_pilot_signal_provider_from_args_rejects_multiple_inputs():
    class Args:
        signal_provider_json_path = "signals.json"
        use_dexscreener_signals = True

    try:
        _build_live_pilot_signal_provider_from_args(Args())
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "use only one signal input" in str(exc)


def test_validate_live_auto_window_guardrails_requires_explicit_flag_for_network_send():
    cfg = {
        "live_send_network_enabled": True,
        "live_send_max_orders_per_session": 1,
        "live_send_max_notional_usd_total": 1.0,
        "manual_submit_mode": "buy_only",
    }
    try:
        _validate_live_auto_window_guardrails(adapter_config=cfg, max_auto_trades=1, explicit_live_auto_submit_enable=False)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "enable-live-auto-submit-window" in str(exc)


def test_validate_live_auto_window_guardrails_requires_single_trade_cap_when_network_send_enabled():
    cfg = {
        "live_send_network_enabled": True,
        "live_send_max_orders_per_session": 1,
        "live_send_max_notional_usd_total": 1.0,
        "manual_submit_mode": "buy_only",
    }
    try:
        _validate_live_auto_window_guardrails(adapter_config=cfg, max_auto_trades=2, explicit_live_auto_submit_enable=True)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "auto-pilot-max-trades 1" in str(exc)


def test_validate_live_auto_window_guardrails_passes_for_s4_m22_4_tiny_live_window():
    cfg = {
        "live_send_network_enabled": True,
        "live_send_max_orders_per_session": 1,
        "live_send_max_notional_usd_total": 1.0,
        "manual_submit_mode": "buy_only",
    }
    _validate_live_auto_window_guardrails(adapter_config=cfg, max_auto_trades=1, explicit_live_auto_submit_enable=True)


def test_apply_live_pilot_mode_preset_sets_no_send_dexscreener_defaults():
    class Args:
        mode = "no_send_dexscreener_auto"
        auto_pilot_window_seconds = 0.0
        auto_pilot_max_trades = 1
        signal_require_fresh_seconds = 0.0
        signal_max_candidates_per_window = 10
        dexscreener_chain_id = "solana"
        dexscreener_min_liquidity_usd = None
        dexscreener_max_pair_age_seconds = None
        use_dexscreener_signals = False
        auto_pilot_stop_on_reconciliation_mismatch = False
        auto_pilot_stop_on_reconciliation_inconclusive = False
        enable_live_auto_submit_window = False

    a = Args()
    _apply_live_pilot_mode_preset(a)
    assert a.use_dexscreener_signals is True
    assert a.auto_pilot_window_seconds == 30.0
    assert a.signal_require_fresh_seconds == 3600.0
    assert a.dexscreener_min_liquidity_usd == 5000.0


def test_apply_live_pilot_mode_preset_sets_live_dexscreener_tiny_one_trade_defaults():
    class Args:
        mode = "live_dexscreener_tiny_one_trade"
        auto_pilot_window_seconds = 0.0
        auto_pilot_max_trades = 1
        signal_require_fresh_seconds = 0.0
        signal_max_candidates_per_window = 10
        dexscreener_chain_id = "solana"
        dexscreener_min_liquidity_usd = None
        dexscreener_max_pair_age_seconds = None
        use_dexscreener_signals = False
        auto_pilot_stop_on_reconciliation_mismatch = False
        auto_pilot_stop_on_reconciliation_inconclusive = False
        enable_live_auto_submit_window = False

    a = Args()
    _apply_live_pilot_mode_preset(a)
    assert a.use_dexscreener_signals is True
    assert a.enable_live_auto_submit_window is True
    assert a.auto_pilot_window_seconds == 30.0
    assert a.auto_pilot_max_trades == 1
    assert a.dexscreener_min_liquidity_usd == 5000.0
    assert a.dexscreener_max_pair_age_seconds == 600.0


def test_build_live_pilot_preflight_reports_dexscreener_missing_url_warning():
    class Args:
        mode = ""
        auto_pilot_window_seconds = 30.0
        auto_pilot_max_trades = 1
        signal_provider_json_path = ""
        use_dexscreener_signals = True
        candidate_list_json_path = ""
        candidate_list_json = ""
        dexscreener_fetch_url = ""
        dexscreener_fallback_urls_json_path = ""
        dexscreener_chain_id = "solana"

    preflight = _build_live_pilot_preflight(
        Args(),
        adapter_config={
            "live_send_network_enabled": False,
            "manual_submit_mode": "buy_only",
            "live_send_max_orders_per_session": 1,
            "live_send_max_notional_usd_total": 1.0,
        },
    )
    assert preflight["ready"] is True
    assert any("DexScreener mode enabled without --dexscreener-fetch-url" in w for w in preflight["warnings"])
    assert any("Network send gate is OFF" in s for s in preflight["info"])


def test_build_live_pilot_preflight_warns_live_dexscreener_missing_tuning():
    class Args:
        mode = "live_dexscreener_tiny_one_trade"
        auto_pilot_window_seconds = 30.0
        auto_pilot_max_trades = 1
        signal_provider_json_path = ""
        use_dexscreener_signals = True
        candidate_list_json_path = ""
        candidate_list_json = ""
        dexscreener_fetch_url = "https://api.dexscreener.com/latest/dex/search/?q=solana"
        dexscreener_fallback_urls_json_path = ""
        dexscreener_chain_id = "solana"
        dexscreener_min_liquidity_usd = None
        dexscreener_max_pair_age_seconds = None

    preflight = _build_live_pilot_preflight(
        Args(),
        adapter_config={
            "live_send_network_enabled": True,
            "manual_submit_mode": "buy_only",
            "live_send_max_orders_per_session": 1,
            "live_send_max_notional_usd_total": 1.0,
        },
    )
    assert preflight["ready"] is False  # explicit live auto submit flag missing by guardrail
    assert any("min-liquidity" in w for w in preflight["warnings"])
    assert any("max-pair-age" in w for w in preflight["warnings"])


def test_build_live_pilot_preflight_validates_dexscreener_fallback_urls_file(tmp_path):
    fallback_path = tmp_path / "fallbacks.json"
    fallback_path.write_text(
        json.dumps(
            [
                "https://example.test/primary",
                "https://example.test/fallback1",
                "https://example.test/fallback1",
            ]
        ),
        encoding="utf-8-sig",
    )

    class Args:
        mode = "no_send_dexscreener_auto"
        auto_pilot_window_seconds = 30.0
        auto_pilot_max_trades = 1
        signal_provider_json_path = ""
        use_dexscreener_signals = True
        candidate_list_json_path = ""
        candidate_list_json = ""
        dexscreener_fetch_url = "https://example.test/primary"
        dexscreener_fallback_urls_json_path = str(fallback_path)
        dexscreener_chain_id = "solana"
        dexscreener_min_liquidity_usd = 5000.0
        dexscreener_max_pair_age_seconds = 600.0

    preflight = _build_live_pilot_preflight(Args(), adapter_config={"live_send_network_enabled": False})
    assert preflight["ready"] is True
    assert any("fallback URLs configured: 3" in s for s in preflight["info"])
    assert any("contains duplicates" in w for w in preflight["warnings"])
    assert any("primary fetch URL is duplicated" in w for w in preflight["warnings"])


def test_build_live_pilot_preflight_flags_invalid_dexscreener_fallback_urls_file(tmp_path):
    fallback_path = tmp_path / "fallbacks.json"
    fallback_path.write_text('{"not":"a-list"}', encoding="utf-8")

    class Args:
        mode = ""
        auto_pilot_window_seconds = 30.0
        auto_pilot_max_trades = 1
        signal_provider_json_path = ""
        use_dexscreener_signals = True
        candidate_list_json_path = ""
        candidate_list_json = ""
        dexscreener_fetch_url = "https://example.test/primary"
        dexscreener_fallback_urls_json_path = str(fallback_path)
        dexscreener_chain_id = "solana"

    preflight = _build_live_pilot_preflight(Args(), adapter_config={"live_send_network_enabled": False})
    assert preflight["ready"] is False
    assert any("Invalid DexScreener fallback URLs file" in e for e in preflight["errors"])


def test_format_human_live_pilot_summary_includes_key_lines():
    txt = _format_human_live_pilot_summary(
        {
            "rollup": {
                "signals_seen": 3,
                "signals_accepted": 2,
                "signals_rejected": 1,
                "candidates_seen": 2,
                "candidates_attempted": 2,
                "candidates_submitted": 0,
                "submitted_signatures": 0,
                "submit_dispatch_by_reason": {"would_send_network_gated": 2},
                "auto_window": {"stop_reason": "candidates_exhausted", "cycles_completed": 2, "trades_submitted": 0},
                "economics_samples_count": 2,
                "fee_lamports_total": 10000,
                "avg_realized_slippage_bps": 42.0,
                "worst_realized_slippage_bps": 75.0,
                "quote_vs_settlement_mismatch_count": 1,
                "signal_provider_metrics": {
                    "fetch_retry_events": 2,
                    "fetch_transport_errors": 1,
                    "fetch_fallback_selected_events": 1,
                    "fetch_endpoint_failure_events": 3,
                    "last_fetch_meta": {"selected_url": "https://example.test/fallback"},
                },
            },
            "live_pilot_summary": {"submit_reason": "would_send_network_gated", "submitted_signature": None},
            "promotion_gate_summary": {"status": "fail", "ready_to_promote": False, "failed_checks": ["min_finalized_pilots"]},
        }
    )
    assert "Live Pilot Summary" in txt
    assert "signals: seen=3 accepted=2 rejected=1" in txt
    assert "would_send_network_gated" in txt
    assert "signal_provider: retries=2 transport_errors=1 fallback_selected=1 endpoint_failures=3" in txt
    assert "signal_provider_last_source: https://example.test/fallback" in txt
    assert "economics:" in txt
    assert "promotion_gate: status=fail ready=False failed=min_finalized_pilots" in txt


def test_run_live_pilot_campaign_aggregates_runs_and_stops_on_transport_error(tmp_path):
    calls = {"n": 0}

    def runner():
        calls["n"] += 1
        n = calls["n"]
        if n == 1:
            return {
                "audit_log_path": f"run{n}.jsonl",
                "rollup": {
                    "runs": 1,
                    "live_finalized_count": 1,
                    "economics_samples_count": 1,
                    "fee_lamports_total": 5000,
                    "avg_realized_slippage_bps": 25.0,
                    "worst_realized_slippage_bps": 25.0,
                    "quote_vs_settlement_mismatch_count": 0,
                    "submit_dispatch_by_reason": {"send_raw_transaction_submitted": 1},
                    "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
                },
                "live_pilot_summary": {"submitted_signature": "SIG1"},
                "promotion_gate_summary": {"status": "fail"},
            }
        return {
            "audit_log_path": f"run{n}.jsonl",
            "rollup": {
                "runs": 1,
                "live_finalized_count": 0,
                "economics_samples_count": 0,
                "submit_dispatch_by_reason": {},
                "signal_provider_metrics": {"fetch_transport_errors": 1, "fetch_endpoint_failure_events": 2},
            },
            "live_pilot_summary": {"submitted_signature": None},
            "promotion_gate_summary": {"status": "fail"},
        }

    report_path = tmp_path / "campaign_report.json"
    state_path = tmp_path / "campaign_state.json"
    out = run_live_pilot_campaign(
        campaign_runs=5,
        run_once_fn=runner,
        campaign_id="camp1",
        campaign_state_json_path=str(state_path),
        campaign_report_path=str(report_path),
        resume_campaign=False,
        promotion_gate_config={"min_finalized_pilots": 1, "max_dexscreener_transport_errors": 0, "min_economics_samples": 1},
    )
    assert calls["n"] == 2
    summary = out["campaign_summary"]
    assert summary["completed_runs"] == 2
    assert summary["stop_reason"] == "signal_provider_transport_error"
    assert summary["aggregate_rollup"]["live_finalized_count"] == 1
    assert summary["aggregate_rollup"]["signal_provider_metrics"]["fetch_endpoint_failure_events"] == 2
    assert summary["promotion_gate_summary"]["status"] == "fail"
    assert report_path.exists()
    assert state_path.exists()


def test_run_live_pilot_campaign_resume_skips_completed_runs_and_writes_markdown(tmp_path):
    state_path = tmp_path / "campaign_state.json"
    report_path = tmp_path / "campaign_report.md"
    state_path.write_text(
        json.dumps(
            {
                "campaign_id": "camp2",
                "target_runs": 3,
                "runs": [
                    {
                            "run_index": 0,
                            "audit_log_path": "a.jsonl",
                            "rollup": {
                                "runs": 1,
                                "live_finalized_count": 1,
                                "economics_samples_count": 1,
                                "fee_lamports_total": 5000,
                                "avg_realized_slippage_bps": 10.0,
                                "worst_realized_slippage_bps": 10.0,
                            },
                        "live_pilot_summary": {"submitted_signature": "SIGA"},
                        "promotion_gate_summary": {"status": "fail"},
                        "submitted_signature": "SIGA",
                    }
                ],
                "stop_reason": "",
            }
        ),
        encoding="utf-8",
    )
    calls = {"n": 0}

    def runner():
        calls["n"] += 1
        return {
            "audit_log_path": f"b{calls['n']}.jsonl",
                "rollup": {
                    "runs": 1,
                    "live_finalized_count": 1,
                    "economics_samples_count": 1,
                    "fee_lamports_total": 4000,
                    "avg_realized_slippage_bps": 20.0,
                    "worst_realized_slippage_bps": 20.0,
                    "submit_dispatch_by_reason": {"send_raw_transaction_submitted": 1},
                    "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
                },
            "live_pilot_summary": {"submitted_signature": f"SIG{calls['n']}"},
            "promotion_gate_summary": {"status": "fail"},
        }

    out = run_live_pilot_campaign(
        campaign_runs=3,
        run_once_fn=runner,
        campaign_id="camp2",
        campaign_state_json_path=str(state_path),
        campaign_report_path=str(report_path),
        resume_campaign=True,
        promotion_gate_config={"min_finalized_pilots": 3, "min_economics_samples": 2},
        stop_evaluator=lambda out: {"stop": False, "reason": ""},
    )
    assert calls["n"] == 2
    assert out["campaign_summary"]["completed_runs"] == 3
    assert out["campaign_summary"]["aggregate_rollup"]["live_finalized_count"] == 3
    assert out["campaign_summary"]["aggregate_rollup"]["fee_lamports_total"] == 13000
    assert out["campaign_summary"]["promotion_gate_summary"]["status"] == "pass"
    md = report_path.read_text(encoding="utf-8")
    assert "# Live Pilot Campaign Report" in md
    assert "ready_to_promote: `True`" in md


def test_run_live_pilot_campaign_emits_alerts_for_hard_stop_and_promotion_gate_fail(tmp_path):
    seen_alerts = []

    def emit(alert):
        seen_alerts.append(dict(alert))

    calls = {"n": 0}
    def runner():
        calls["n"] += 1
        return {
            "audit_log_path": "r1.jsonl",
            "rollup": {
                "runs": 1,
                "live_reconciliation_mismatch_count": 1,
                "pause_latch_events": 0,
                "economics_samples_count": 1,
                "fee_lamports_total": 5000,
                "avg_realized_slippage_bps": 50.0,
                "worst_realized_slippage_bps": 50.0,
                "signal_provider_metrics": {"fetch_transport_errors": 1, "fetch_endpoint_failure_events": 2, "last_error": "HTTP 403"},
            },
            "live_pilot_summary": {"submitted_signature": "SIG1"},
            "promotion_gate_summary": {"status": "fail"},
        }

    report_path = tmp_path / "campaign_alert_report.json"
    out = run_live_pilot_campaign(
        campaign_runs=3,
        run_once_fn=runner,
        campaign_id="camp_alerts",
        campaign_report_path=str(report_path),
        promotion_gate_config={"min_finalized_pilots": 1, "min_economics_samples": 1},
        alert_emitter=emit,
        alert_policy={"max_run_transport_errors_before_critical": 0, "max_run_endpoint_failures_before_critical": 10},
        alert_on_campaign_stop=True,
        alert_on_promotion_gate_fail=True,
    )
    assert calls["n"] == 1
    assert any(a["alert_type"] == "reconciliation_mismatch" for a in seen_alerts)
    assert any(a["alert_type"] == "dexscreener_transport_errors" for a in seen_alerts)
    assert any(a["alert_type"] == "campaign_hard_stop" for a in seen_alerts)
    assert any(a["alert_type"] == "promotion_gate_failed" for a in seen_alerts)
    assert out["campaign_summary"]["alert_summary"]["count"] >= 4
    on_disk = json.loads(report_path.read_text(encoding="utf-8"))
    assert on_disk["campaign_summary"]["alert_summary"]["count"] >= 4
    assert "promotion_gate_failed" in on_disk["campaign_summary"]["alert_summary"]["by_type"]


def test_run_live_pilot_campaign_no_alerts_when_disabled(tmp_path):
    seen_alerts = []

    def runner():
        return {
            "audit_log_path": "r1.jsonl",
            "rollup": {
                "runs": 1,
                "live_finalized_count": 1,
                "economics_samples_count": 1,
                "fee_lamports_total": 5000,
                "avg_realized_slippage_bps": 10.0,
                "worst_realized_slippage_bps": 10.0,
                "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
            },
            "live_pilot_summary": {"submitted_signature": "SIG1"},
            "promotion_gate_summary": {"status": "pass"},
        }

    out = run_live_pilot_campaign(
        campaign_runs=1,
        run_once_fn=runner,
        campaign_id="camp_noalerts",
        promotion_gate_config={"min_finalized_pilots": 1, "min_economics_samples": 1},
        alert_emitter=lambda a: seen_alerts.append(a),
        stop_evaluator=lambda out: {"stop": False, "reason": ""},
        alert_on_campaign_stop=False,
        alert_on_promotion_gate_fail=False,
    )
    assert seen_alerts == []
    assert out["campaign_summary"]["alert_summary"]["count"] == 0


def test_run_live_pilot_campaign_resume_without_new_runs_suppresses_completion_alerts(tmp_path):
    state_path = tmp_path / "camp_state.json"
    state_path.write_text(
        json.dumps({"campaign_id": "camp_resume", "target_runs": 1, "runs": [], "stop_reason": "manual_stop"}),
        encoding="utf-8",
    )
    seen = []
    out = run_live_pilot_campaign(
        campaign_runs=1,
        run_once_fn=lambda: (_ for _ in ()).throw(RuntimeError("should not run")),
        campaign_id="camp_resume",
        campaign_state_json_path=str(state_path),
        resume_campaign=True,
        alert_emitter=lambda a: seen.append(a),
        alert_on_promotion_gate_fail=True,
        alert_on_campaign_stop=True,
    )
    assert out["campaign_summary"]["completed_runs"] == 0
    assert seen == []
    assert out["campaign_summary"]["alert_summary"]["count"] == 0


def test_aggregate_live_pilot_campaign_reports_produces_recommendation_and_trends(tmp_path):
    reports = [
        {
            "campaign_summary": {
                "campaign_id": "c1",
                "target_runs": 3,
                "completed_runs": 3,
                "stop_reason": "",
                "aggregate_rollup": {
                    "live_finalized_count": 1,
                    "live_reconciliation_mismatch_count": 0,
                    "economics_samples_count": 1,
                    "fee_lamports_total": 5000,
                    "avg_realized_slippage_bps": 30.0,
                    "worst_realized_slippage_bps": 40.0,
                    "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
                },
                "promotion_gate_summary": {"status": "pass", "ready_to_promote": True, "failed_checks": []},
                "alert_summary": {"count": 0, "by_level": {}},
            }
        },
        {
            "campaign_summary": {
                "campaign_id": "c2",
                "target_runs": 3,
                "completed_runs": 3,
                "stop_reason": "",
                "aggregate_rollup": {
                    "live_finalized_count": 1,
                    "live_reconciliation_mismatch_count": 0,
                    "economics_samples_count": 1,
                    "fee_lamports_total": 4500,
                    "avg_realized_slippage_bps": 25.0,
                    "worst_realized_slippage_bps": 35.0,
                    "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
                },
                "promotion_gate_summary": {"status": "pass", "ready_to_promote": True, "failed_checks": []},
                "alert_summary": {"count": 0, "by_level": {}},
            }
        },
        {
            "campaign_summary": {
                "campaign_id": "c3",
                "target_runs": 3,
                "completed_runs": 3,
                "stop_reason": "",
                "aggregate_rollup": {
                    "live_finalized_count": 2,
                    "live_reconciliation_mismatch_count": 0,
                    "economics_samples_count": 2,
                    "fee_lamports_total": 9000,
                    "avg_realized_slippage_bps": 20.0,
                    "worst_realized_slippage_bps": 30.0,
                    "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
                },
                "promotion_gate_summary": {"status": "pass", "ready_to_promote": True, "failed_checks": []},
                "alert_summary": {"count": 0, "by_level": {}},
            }
        },
    ]
    trend = aggregate_live_pilot_campaign_reports(reports)
    assert trend["campaign_count"] == 3
    assert trend["aggregate"]["live_finalized_count_total"] == 4
    assert trend["trends"]["promotion_gate_status_sequence"] == ["pass", "pass", "pass"]
    assert trend["recommendation"]["action"] == "increase_cap_small_step"

    md_path = tmp_path / "trend_report.md"
    write_campaign_trend_report(trend, str(md_path))
    md = md_path.read_text(encoding="utf-8")
    assert "# Live Pilot Campaign Trend Report" in md
    assert "increase_cap_small_step" in md


def test_run_live_pilot_campaign_tracks_discovery_provider_failover_and_alert(tmp_path):
    alerts = []
    calls = {"n": 0}

    def runner():
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "audit_log_path": "r1.jsonl",
                "rollup": {"signal_provider_metrics": {"fetch_transport_errors": 1, "fetch_endpoint_failure_events": 2}},
                "live_pilot_summary": {},
                "promotion_gate_summary": {"status": "fail"},
                "campaign_provider": {
                    "provider": "candidate_file",
                    "from_provider": "dexscreener",
                    "failover_applied": True,
                    "failover_reason": "signal_provider_transport_error",
                },
            }
        return {
            "audit_log_path": "r2.jsonl",
            "rollup": {"candidates_seen": 1, "candidates_attempted": 1, "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0}},
            "live_pilot_summary": {},
            "promotion_gate_summary": {"status": "fail"},
            "campaign_provider": {"provider": "candidate_file"},
        }

    out = run_live_pilot_campaign(
        campaign_runs=2,
        run_once_fn=runner,
        campaign_id="camp_failover",
        stop_evaluator=lambda out: {"stop": False, "reason": ""},
        alert_emitter=lambda a: alerts.append(dict(a)),
    )
    dps = out["campaign_summary"]["discovery_provider_summary"]
    assert dps["provider_failover_count"] == 1
    assert dps["provider_usage_by_provider"]["candidate_file"] == 2
    assert dps["per_provider_metrics"]["candidate_file"]["candidates_attempted"] == 1
    assert any(a["alert_type"] == "discovery_provider_failover" for a in alerts)


def test_aggregate_live_pilot_campaign_reports_recommends_fallback_source_when_provider_issues_but_failovers_present():
    reports = [
        {
            "campaign_summary": {
                "campaign_id": "c1",
                "target_runs": 2,
                "completed_runs": 2,
                "stop_reason": "",
                "aggregate_rollup": {
                    "live_finalized_count": 1,
                    "live_reconciliation_mismatch_count": 0,
                    "economics_samples_count": 1,
                    "fee_lamports_total": 5000,
                    "avg_realized_slippage_bps": 30.0,
                    "worst_realized_slippage_bps": 30.0,
                    "signal_provider_metrics": {"fetch_transport_errors": 1, "fetch_endpoint_failure_events": 2},
                },
                "promotion_gate_summary": {"status": "pass", "ready_to_promote": True, "failed_checks": []},
                "alert_summary": {"count": 1, "by_level": {"warning": 1}},
                "discovery_provider_summary": {
                    "provider_failover_count": 1,
                    "provider_usage_by_provider": {"dexscreener": 1, "candidate_file": 1},
                    "per_provider_metrics": {},
                },
            }
        },
        {
            "campaign_summary": {
                "campaign_id": "c2",
                "target_runs": 2,
                "completed_runs": 2,
                "stop_reason": "",
                "aggregate_rollup": {
                    "live_finalized_count": 2,
                    "live_reconciliation_mismatch_count": 0,
                    "economics_samples_count": 2,
                    "fee_lamports_total": 10000,
                    "avg_realized_slippage_bps": 25.0,
                    "worst_realized_slippage_bps": 40.0,
                    "signal_provider_metrics": {"fetch_transport_errors": 1, "fetch_endpoint_failure_events": 2},
                },
                "promotion_gate_summary": {"status": "pass", "ready_to_promote": True, "failed_checks": []},
                "alert_summary": {"count": 1, "by_level": {"warning": 1}},
                "discovery_provider_summary": {
                    "provider_failover_count": 1,
                    "provider_usage_by_provider": {"dexscreener": 1, "candidate_file": 1},
                    "per_provider_metrics": {},
                },
            }
        },
    ]
    trend = aggregate_live_pilot_campaign_reports(
        reports,
        recommendation_config={
            "min_campaigns": 2,
            "min_total_finalized": 2,
            "max_total_dexscreener_transport_errors": 0,
            "max_total_reconciliation_mismatches": 0,
            "require_recent_gate_passes": 2,
        },
    )
    assert trend["aggregate"]["provider_failover_count_total"] == 2
    assert trend["aggregate"]["provider_usage_by_provider"]["candidate_file"] == 2
    assert trend["recommendation"]["action"] == "continue_tiny_pilots_with_fallback_source"


def test_sanitize_fallback_candidates_normalizes_and_drops_invalid():
    out = sanitize_fallback_candidates(
        [
            {"token_address": "A", "symbol": "", "entry_price": 0, "usd_size": -1},
            {"symbol": "NOADDR"},
            "bad",
        ]
    )
    assert len(out["candidates"]) == 1
    c = out["candidates"][0]
    assert c["symbol"] == "A"
    assert c["entry_price"] == 1.0
    assert c["usd_size"] == 1.0
    s = out["summary"]
    assert s["fallback_candidates_total"] == 3
    assert s["fallback_candidates_sanitized_count"] == 1
    assert s["fallback_candidates_dropped_count"] == 2


def test_probe_fallback_candidates_preflight_filters_failed_probes_and_can_fail_closed():
    candidates = [
        {"token_address": "A", "symbol": "A", "entry_price": 1.0, "usd_size": 1.0},
        {"token_address": "B", "symbol": "B", "entry_price": 1.0, "usd_size": 1.0},
        {"token_address": "C", "symbol": "C", "entry_price": 1.0, "usd_size": 1.0},
    ]
    seen = []

    def probe_fn(c):
        seen.append(c["token_address"])
        return {"ok": c["token_address"] != "B", "reason": "quote_400" if c["token_address"] == "B" else ""}

    out = probe_fallback_candidates_preflight(
        candidates,
        probe_count=2,
        quote_probe_fail_closed=True,
        quote_probe_min_pass_rate=0.75,
        probe_fn=probe_fn,
    )
    assert seen == ["A", "B"]
    kept = out["candidates"]
    assert [c["token_address"] for c in kept] == ["A", "C"]
    s = out["summary"]
    assert s["fallback_candidates_probe_ok"] == 1
    assert s["fallback_candidates_probe_failed"] == 1
    assert s["fallback_candidates_probe_skipped"] == 1
    assert s["fallback_candidates_probe_pass_rate"] == 0.5
    assert s["fallback_candidates_probe_fail_closed_triggered"] is True


def test_run_live_pilot_campaign_accepts_initial_alerts_and_extra_summary():
    out = run_live_pilot_campaign(
        campaign_runs=1,
        run_once_fn=lambda: {"audit_log_path": "x", "rollup": {}, "live_pilot_summary": {}, "promotion_gate_summary": {}},
        campaign_id="camp_extra",
        stop_evaluator=lambda out: {"stop": False, "reason": ""},
        initial_alerts=[{"alert_type": "fallback_candidate_probe_failure_rate_high", "level": "warning", "message": "x"}],
        campaign_extra_summary={"fallback_candidate_probe_summary": {"fallback_candidates_probe_failed": 2, "fallback_candidates_probe_ok": 1}},
    )
    assert out["campaign_summary"]["alert_summary"]["count"] >= 1
    assert out["campaign_summary"]["fallback_candidate_probe_summary"]["fallback_candidates_probe_failed"] == 2


def test_extract_campaign_report_summary_includes_fallback_probe_fields():
    s = _extract_campaign_report_summary(
        {
            "campaign_summary": {
                "campaign_id": "c1",
                "aggregate_rollup": {},
                "promotion_gate_summary": {},
                "alert_summary": {},
                "discovery_provider_summary": {},
                "fallback_candidate_probe_summary": {
                    "fallback_candidates_total": 10,
                    "fallback_candidates_probe_ok": 3,
                    "fallback_candidates_probe_failed": 2,
                },
            }
        }
    )
    assert s["fallback_candidates_total"] == 10
    assert s["fallback_candidates_probe_ok"] == 3
    assert s["fallback_candidates_probe_failed"] == 2


def test_adaptive_reorder_fallback_candidates_scores_and_quarantines():
    state = {
        "candidate_stats": {
            "GOOD": {"probe_ok": 3, "probe_failed": 0},
            "BAD": {"probe_ok": 0, "probe_failed": 4},
        }
    }
    candidates = [
        {"token_address": "BAD", "symbol": "BAD", "entry_price": 1, "usd_size": 1, "metadata": {}},
        {"token_address": "GOOD", "symbol": "GOOD", "entry_price": 1, "usd_size": 1, "metadata": {}},
        {"token_address": "MID", "symbol": "MID", "entry_price": 1, "usd_size": 1, "metadata": {"confidence_hint": 1}},
    ]
    out = adaptive_reorder_fallback_candidates(candidates, reliability_state=state, enabled=True, quarantine_failure_threshold=3)
    ordered = [c["token_address"] for c in out["candidates"]]
    assert ordered == ["GOOD", "MID"]
    s = out["summary"]
    assert s["adaptive_candidate_reordering_applied"] is True
    assert s["adaptive_candidate_quarantined_count"] == 1
    assert "BAD" in s["adaptive_candidate_quarantined_tokens"]


def test_adaptive_reorder_provider_order_uses_reliability_state():
    state = {
        "provider_stats": {
            "dexscreener": {"transport_errors": 3, "hard_stops": 1, "successful_runs": 0},
            "candidate_file": {"transport_errors": 0, "hard_stops": 0, "successful_runs": 2},
        }
    }
    out = adaptive_reorder_provider_order(["dexscreener", "candidate_file"], reliability_state=state, enabled=True)
    assert out["provider_order"] == ["candidate_file", "dexscreener"]
    assert out["summary"]["adaptive_provider_order_applied"] is True


def test_update_adaptive_reliability_state_from_campaign_report_tracks_probe_and_provider_stats(tmp_path):
    state = {"candidate_stats": {}, "provider_stats": {}, "meta": {}}
    report = {
        "campaign_summary": {
            "fallback_candidate_probe_summary": {
                "fallback_candidates_probe_successes": [{"token_address": "A"}],
                "fallback_candidates_probe_failures": [{"token_address": "B", "reason": "quote_400"}],
            }
        },
        "runs": [
            {"campaign_provider": {"executed_provider": "dexscreener"}, "rollup": {"signal_provider_metrics": {"fetch_transport_errors": 1, "fetch_endpoint_failure_events": 2}}},
            {"campaign_provider": {"executed_provider": "candidate_file", "execution_error": True}, "rollup": {}},
        ],
    }
    out = update_adaptive_reliability_state_from_campaign_report(state, report)
    assert out["candidate_stats"]["A"]["probe_ok"] == 1
    assert out["candidate_stats"]["B"]["probe_failed"] == 1
    assert out["provider_stats"]["dexscreener"]["transport_errors"] == 1
    assert out["provider_stats"]["candidate_file"]["hard_stops"] == 1

    p = tmp_path / "adaptive_state.json"
    save_adaptive_reliability_state(str(p), out)
    loaded = load_adaptive_reliability_state(str(p))
    assert loaded["candidate_stats"]["A"]["probe_ok"] == 1


def test_extract_campaign_report_summary_includes_adaptive_fields():
    s = _extract_campaign_report_summary(
        {
            "campaign_summary": {
                "aggregate_rollup": {},
                "promotion_gate_summary": {},
                "alert_summary": {},
                "discovery_provider_summary": {},
                "fallback_candidate_probe_summary": {},
                "adaptive_fallback_candidate_summary": {
                    "adaptive_candidate_reordering_applied": True,
                    "adaptive_candidate_quarantined_count": 2,
                },
                "adaptive_provider_order_summary": {
                    "adaptive_provider_order_applied": True,
                },
            }
        }
    )
    assert s["adaptive_candidate_reordering_applied"] is True
    assert s["adaptive_candidate_quarantined_count"] == 2
    assert s["adaptive_provider_order_applied"] is True


def test_adaptive_reorder_fallback_candidates_respects_quarantine_ttl():
    old_ms = 1
    state = {
        "candidate_stats": {
            "OLD_BAD": {"probe_failed": 4, "last_negative_unix_ms": old_ms},
            "NEW_BAD": {"probe_failed": 4, "last_negative_unix_ms": 9999999999999},
        }
    }
    candidates = [
        {"token_address": "OLD_BAD", "symbol": "OLD_BAD", "entry_price": 1, "usd_size": 1},
        {"token_address": "NEW_BAD", "symbol": "NEW_BAD", "entry_price": 1, "usd_size": 1},
    ]
    out = adaptive_reorder_fallback_candidates(
        candidates,
        reliability_state=state,
        enabled=True,
        quarantine_failure_threshold=3,
        quarantine_ttl_seconds=60.0,
    )
    ordered = [c["token_address"] for c in out["candidates"]]
    assert ordered == ["OLD_BAD"]
    assert out["summary"]["adaptive_candidate_quarantined_count"] == 1
    assert out["summary"]["adaptive_candidate_ttl_expired_count"] == 1


def test_update_adaptive_reliability_state_from_campaign_report_applies_decay():
    state = {
        "candidate_stats": {"A": {"probe_ok": 10}},
        "provider_stats": {"dexscreener": {"successful_runs": 10}},
        "meta": {"candidate_decay_factor": 0.5, "provider_decay_factor": 0.5},
    }
    report = {"campaign_summary": {"fallback_candidate_probe_summary": {}}, "runs": []}
    out = update_adaptive_reliability_state_from_campaign_report(state, report)
    assert out["candidate_stats"]["A"]["probe_ok"] == 5.0
    assert out["provider_stats"]["dexscreener"]["successful_runs"] == 5.0
    assert "last_decay_applied_unix_ms" in out["meta"]


def test_promotion_gate_can_fail_on_adaptive_quarantine_metrics():
    rollup = {
        "live_finalized_count": 3,
        "live_reconciliation_mismatch_count": 0,
        "pause_latch_events": 0,
        "quote_vs_settlement_mismatch_count": 0,
        "economics_samples_count": 1,
        "worst_realized_slippage_bps": 10.0,
        "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
        "adaptive_candidate_quarantined_count": 2,
        "adaptive_fallback_quality_degraded_events": 1,
    }
    gate = _evaluate_live_pilot_promotion_gates(
        rollup,
        {
            "max_adaptive_candidate_quarantined_count": 1,
            "max_adaptive_fallback_quality_degraded_events": 0,
        },
    )
    assert gate["status"] == "fail"
    assert "max_adaptive_candidate_quarantined_count" in gate["failed_checks"]
    assert "max_adaptive_fallback_quality_degraded_events" in gate["failed_checks"]


def test_run_live_pilot_campaign_schedule_timebox_and_resume(tmp_path):
    calls = []
    fake_now = {"t": 1000.0}

    def _now():
        return fake_now["t"]

    def _sleep(seconds):
        fake_now["t"] += float(seconds)

    def _run_campaign(session_index):
        calls.append(session_index)
        fake_now["t"] += 5.0
        return {
            "campaign_summary": {
                "campaign_id": f"c{session_index}",
                "completed_runs": 1,
                "stop_reason": "",
                "aggregate_rollup": {
                    "live_finalized_count": 0,
                    "live_reconciliation_mismatch_count": 0,
                    "economics_samples_count": 0,
                    "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
                },
                "promotion_gate_summary": {"status": "fail", "ready_to_promote": False, "failed_checks": ["min_finalized_pilots"]},
                "alert_summary": {},
                "discovery_provider_summary": {},
            },
            "report_path": "",
            "state_path": "",
        }

    schedule_state = tmp_path / "sched_state.json"
    out = run_live_pilot_campaign_schedule(
        target_sessions=3,
        run_campaign_fn=_run_campaign,
        schedule_id="sched1",
        session_interval_seconds=10.0,
        schedule_max_duration_seconds=15.0,
        schedule_state_json_path=str(schedule_state),
        now_fn=_now,
        sleep_fn=_sleep,
    )
    assert out["schedule_summary"]["completed_sessions"] == 1
    assert out["schedule_summary"]["stop_reason"] == "schedule_timebox_elapsed"
    assert calls == [0]

    fake_now["t"] = 2000.0
    out2 = run_live_pilot_campaign_schedule(
        target_sessions=3,
        run_campaign_fn=_run_campaign,
        schedule_id="sched1",
        schedule_state_json_path=str(schedule_state),
        resume_schedule=True,
        now_fn=_now,
        sleep_fn=_sleep,
    )
    assert out2["schedule_summary"]["completed_sessions"] == 3
    assert calls == [0, 1, 2]


def test_build_live_pilot_daily_operator_report_and_write_markdown(tmp_path):
    reports = [
        {
            "campaign_summary": {
                "campaign_id": "a",
                "completed_runs": 1,
                "stop_reason": "",
                "aggregate_rollup": {
                    "live_finalized_count": 1,
                    "live_reconciliation_mismatch_count": 0,
                    "economics_samples_count": 1,
                    "worst_realized_slippage_bps": 5.0,
                    "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
                },
                "promotion_gate_summary": {"status": "pass", "ready_to_promote": True, "failed_checks": []},
                "alert_summary": {"by_level": {}},
                "discovery_provider_summary": {"provider_usage_by_provider": {"candidate_file": 1}, "provider_failover_count": 0},
                "fallback_candidate_probe_summary": {"fallback_candidates_probe_ok": 1, "fallback_candidates_probe_failed": 0},
            }
        },
        {
            "campaign_summary": {
                "campaign_id": "b",
                "completed_runs": 1,
                "stop_reason": "",
                "aggregate_rollup": {
                    "live_finalized_count": 2,
                    "live_reconciliation_mismatch_count": 0,
                    "economics_samples_count": 1,
                    "worst_realized_slippage_bps": 6.0,
                    "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
                },
                "promotion_gate_summary": {"status": "pass", "ready_to_promote": True, "failed_checks": []},
                "alert_summary": {"by_level": {}},
                "discovery_provider_summary": {"provider_usage_by_provider": {"candidate_file": 1}, "provider_failover_count": 0},
                "fallback_candidate_probe_summary": {"fallback_candidates_probe_ok": 1, "fallback_candidates_probe_failed": 0},
            }
        },
        {
            "campaign_summary": {
                "campaign_id": "c",
                "completed_runs": 1,
                "stop_reason": "",
                "aggregate_rollup": {
                    "live_finalized_count": 3,
                    "live_reconciliation_mismatch_count": 0,
                    "economics_samples_count": 1,
                    "worst_realized_slippage_bps": 7.0,
                    "signal_provider_metrics": {"fetch_transport_errors": 0, "fetch_endpoint_failure_events": 0},
                },
                "promotion_gate_summary": {"status": "pass", "ready_to_promote": True, "failed_checks": []},
                "alert_summary": {"by_level": {}},
                "discovery_provider_summary": {"provider_usage_by_provider": {"candidate_file": 1}, "provider_failover_count": 0},
                "fallback_candidate_probe_summary": {"fallback_candidates_probe_ok": 1, "fallback_candidates_probe_failed": 0},
            }
        },
    ]
    out = build_live_pilot_daily_operator_report(reports, date_label="2026-02-25")
    assert out["operator_decision_summary"]["recommended_action"] in {"increase_cap_small_step", "continue_tiny_pilots", "hold"}
    assert out["operator_decision_summary"]["decision_status"] in {
        "eligible_for_operator_promotion_review",
        "continue_supervised_validation",
        "manual_review_required",
    }
    path = tmp_path / "daily_report.md"
    write_live_pilot_daily_operator_report(out, str(path))
    txt = path.read_text(encoding="utf-8")
    assert "Live Pilot Daily Operator Report" in txt
    assert "Operator Checklist" in txt


def test_campaign_alert_emitter_quiet_hours_suppresses_noncritical_and_escalates(tmp_path, capsys):
    alerts_path = tmp_path / "alerts.jsonl"
    emit = _build_campaign_alert_emitter(
        campaign_id="c1",
        alerts_jsonl_path=str(alerts_path),
        console=True,
        quiet_hours_start_hour_utc=0,
        quiet_hours_end_hour_utc=0,  # treated as always quiet for deterministic testing
        allow_critical_during_quiet_hours=True,
        escalation_levels={"warning": "critical"},
    )
    emit({"alert_type": "warn1", "level": "warning", "message": "hello"})
    out = capsys.readouterr().out
    assert "[campaign-alert] critical warn1: hello" in out

    emit2 = _build_campaign_alert_emitter(
        campaign_id="c2",
        alerts_jsonl_path=str(alerts_path),
        console=True,
        quiet_hours_start_hour_utc=0,
        quiet_hours_end_hour_utc=0,
        allow_critical_during_quiet_hours=False,
    )
    emit2({"alert_type": "warn2", "level": "warning", "message": "quiet"})
    out2 = capsys.readouterr().out
    assert "warn2" not in out2

    rows = [json.loads(x) for x in alerts_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows[0]["level"] == "critical"
    assert rows[0]["base_level"] == "warning"
    assert rows[0]["quiet_hours_active"] is True
    assert rows[0]["console_suppressed_by_quiet_hours"] is False
    assert rows[1]["console_suppressed_by_quiet_hours"] is True


def test_operator_decision_log_and_acknowledgement_are_embedded_in_daily_report(tmp_path):
    report = build_live_pilot_daily_operator_report([], date_label="2026-02-25")
    log_path = tmp_path / "decision_log.jsonl"
    row = append_live_pilot_operator_decision_log(
        path_str=str(log_path),
        daily_report=report,
        operator_id="main_user",
        action="hold",
        notes="Waiting for more clean campaigns",
        now_unix_ms=123456789,
    )
    out = apply_operator_acknowledgement_to_daily_report(report, row)
    md_path = tmp_path / "daily.md"
    write_live_pilot_daily_operator_report(out, str(md_path))
    txt = md_path.read_text(encoding="utf-8")
    assert "Operator Acknowledgement" in txt
    assert "main_user" in txt
    assert "Waiting for more clean campaigns" in txt
    rows = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows[0]["event_type"] == "live_pilot_operator_decision"
    assert rows[0]["action"] == "hold"


def test_build_artifact_index_and_handoff_snapshot_writers(tmp_path):
    daily_path = tmp_path / "daily.md"
    daily_path.write_text("x", encoding="utf-8")
    sched_path = tmp_path / "schedule.md"
    sched_path.write_text("y", encoding="utf-8")
    alert_path = tmp_path / "alerts.jsonl"
    alert_path.write_text("", encoding="utf-8")
    daily_report = {
        "date_label": "2026-02-25",
        "latest_campaign_summary": {"campaign_id": "c1", "stop_reason": ""},
        "operator_decision_summary": {"recommended_action": "continue_tiny_pilots", "decision_status": "continue_supervised_validation"},
    }
    schedule_report = {"schedule_summary": {"schedule_id": "s1", "completed_sessions": 1, "stop_reason": ""}, "sessions": []}
    idx = build_live_pilot_artifact_index(
        date_label="2026-02-25",
        schedule_report=schedule_report,
        schedule_report_path=str(sched_path),
        daily_operator_report=daily_report,
        daily_operator_report_path=str(daily_path),
        alerts_jsonl_path=str(alert_path),
        campaign_reports=[{"campaign_summary": {"campaign_id": "c1"}}],
    )
    assert idx["artifacts"]["daily_operator_report"]["present"] is True
    assert idx["artifacts"]["schedule_report"]["present"] is True
    idx_path = tmp_path / "artifact_index.md"
    write_live_pilot_artifact_index(idx, str(idx_path))
    assert "Live Pilot Artifact Index" in idx_path.read_text(encoding="utf-8")

    handoff = build_live_pilot_handoff_snapshot(
        schedule_report=schedule_report,
        daily_operator_report=daily_report,
        artifact_index={"date_label": "2026-02-25", "artifacts": {"artifact_index": {"path": str(idx_path)}}},
        handoff_operator_id="main_user",
        shift_label="night_shift",
        handoff_notes="All good",
        restart_command_hint="python -m src.live.live_pilot_service ... --resume-schedule",
    )
    assert handoff["schedule_summary"]["schedule_id"] == "s1"
    h_path = tmp_path / "handoff.md"
    write_live_pilot_handoff_snapshot(handoff, str(h_path))
    txt = h_path.read_text(encoding="utf-8")
    assert "Operator Hand-off Snapshot" in txt
    assert "Restart Recovery Checklist" in txt


def test_resume_state_validators_detect_duplicate_indexes():
    c = validate_live_pilot_campaign_state({"target_runs": 2, "runs": [{"run_index": 0}, {"run_index": 0}]})
    s = validate_live_pilot_schedule_state({"target_sessions": 2, "sessions": [{"session_index": 0}, {"session_index": 0}]})
    assert c["ok"] is False
    assert "duplicate_run_index" in c["errors"]
    assert s["ok"] is False
    assert "duplicate_session_index" in s["errors"]


def test_run_manifest_writer_redacts_tokens_and_includes_repro_command(tmp_path):
    class _Args:
        def __init__(self):
            self.mode = "pilot_campaign_tiny_supervised"
            self.token_address = "TOKEN123"
            self.manual_submit_required_token = "secret"

    m = build_live_pilot_run_manifest(args_namespace=_Args(), argv=["--mode", "pilot_campaign_tiny_supervised"], phase="pre_run")
    assert "python -m src.live.live_pilot_service" in m["repro_command"]
    assert m["args"]["manual_submit_required_token"] == "***"
    p = tmp_path / "manifest.md"
    write_live_pilot_run_manifest(m, str(p))
    assert "Run Manifest" in p.read_text(encoding="utf-8")


def test_bundle_verification_and_timeline_writers(tmp_path):
    sched = tmp_path / "schedule.md"
    sched.write_text("x", encoding="utf-8")
    daily = tmp_path / "daily.md"
    daily.write_text("x", encoding="utf-8")
    idx = {
        "artifacts": {
            "schedule_report": {"path": str(sched), "present": True},
            "schedule_state": {"path": str(tmp_path / "missing.json"), "present": False},
            "daily_operator_report": {"path": str(daily), "present": True},
            "alerts_jsonl": {"path": "", "present": False},
            "operator_decision_log_jsonl": {"path": "", "present": False},
            "campaign_reports": [],
            "campaign_states": [],
        }
    }
    v = verify_live_pilot_validation_bundle(artifact_index=idx)
    assert v["status"] == "fail"
    assert "schedule_state" in v["failed_checks"]
    vp = tmp_path / "bundle_verify.md"
    write_live_pilot_bundle_verification(v, str(vp))
    assert "Validation Bundle Verification" in vp.read_text(encoding="utf-8")

    timeline = build_live_pilot_session_timeline(
        schedule_report={"schedule_summary": {"schedule_id": "s1"}, "sessions": [{"session_index": 0, "campaign_id": "c1", "campaign_summary": {"stop_reason": "x"}}]},
        alerts_rows=[{"ts_unix_ms": 10, "alert_type": "a", "level": "critical", "message": "m"}],
        operator_decision_rows=[{"ts_unix_ms": 20, "operator_id": "u", "action": "hold", "notes": "n"}],
    )
    assert timeline["event_count"] >= 3
    assert any(e["event_type"] == "operator_decision" for e in timeline["incident_breadcrumbs"])
    tp = tmp_path / "timeline.md"
    write_live_pilot_session_timeline(timeline, str(tp))
    assert "Session Timeline" in tp.read_text(encoding="utf-8")


def test_risk_profile_preset_and_promotion_manifest_writer(tmp_path):
    preset = get_live_pilot_risk_profile_preset("tiny_supervised")
    assert preset["max_notional_usd_total"] == 1.0
    manifest = build_live_pilot_promotion_step_manifest(
        risk_profile_preset="tiny_supervised",
        step_name="increase_frequency_only",
        daily_operator_report={"operator_decision_summary": {"recommended_action": "continue_tiny_pilots", "decision_status": "continue_supervised_validation"}},
        artifact_index={"artifacts": {"artifact_index": {"path": "x"}}},
        bundle_verification={"status": "pass", "failed_checks": []},
        operator_decision_log_path="decisions.jsonl",
    )
    assert manifest["risk_profile"]["profile_name"] == "tiny_supervised"
    p = tmp_path / "promotion_manifest.md"
    write_live_pilot_promotion_step_manifest(manifest, str(p))
    assert "Promotion Step Manifest" in p.read_text(encoding="utf-8")


def test_prelive_go_no_go_checklist_writer(tmp_path):
    report = build_live_pilot_prelive_go_no_go_checklist(
        daily_operator_report={
            "operator_decision_summary": {"recommended_action": "continue_tiny_pilots"},
            "operator_acknowledgement": {"action": "continue_tiny_pilots"},
        },
        bundle_verification={"status": "pass"},
        handoff_snapshot={"schedule_summary": {"schedule_id": "s1"}},
        risk_profile_preset="tiny_supervised",
        required_operator_ack=True,
        require_bundle_pass=True,
    )
    assert report["status"] == "go"
    p = tmp_path / "go_no_go.md"
    write_live_pilot_prelive_go_no_go_checklist(report, str(p))
    txt = p.read_text(encoding="utf-8")
    assert "Pre-Live Go / No-Go Checklist" in txt
    assert "status: `go`" in txt
