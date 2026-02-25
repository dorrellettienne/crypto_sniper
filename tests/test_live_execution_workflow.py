import pytest

from src.live.confirmation_retry_policy import ConfirmationRetryPolicy
from src.live.live_execution_workflow import (
    build_execution_preview_workflow,
    build_workflow_identifiers,
)


def test_build_workflow_identifiers_buy_is_deterministic():
    first = build_workflow_identifiers(
        action="buy",
        token_address="TOKEN",
        symbol="SYM",
        entry_price=0.01,
        usd_size=100,
    )
    second = build_workflow_identifiers(
        action="buy",
        token_address="TOKEN",
        symbol="SYM",
        entry_price=0.01,
        usd_size=100,
    )

    assert first == second
    assert first["client_order_id"].startswith("coid_buy_")
    assert len(first["request_fingerprint"]) > 20


def test_build_execution_preview_workflow_retry_path_includes_lifecycle_and_decision():
    workflow = build_execution_preview_workflow(
        action="buy",
        order_preview={"action": "buy"},
        rpc_health={"ok": True},
        client_order_id="coid_test",
        request_fingerprint="req_test",
        policy=ConfirmationRetryPolicy(max_attempts=3, timeout_seconds=10, backoff_mode="linear", backoff_base_seconds=0.5),
        elapsed_seconds=0.0,
        simulated_outcome="retry",
    )

    assert workflow["final_decision"] == "retry"
    assert workflow["retry_policy_decision"]["backoff_seconds"] == 1.0
    assert len(workflow["lifecycle_events"]) == 2
    assert workflow["lifecycle_events"][0]["status"] == "submitted"
    assert workflow["lifecycle_events"][1]["status"] == "retrying"
    assert workflow["reconciliation"]["final_status"] == "retrying"
    assert workflow["reconciliation"]["final_decision"] == "retry"


def test_build_execution_preview_workflow_timeout_path():
    workflow = build_execution_preview_workflow(
        action="sell",
        order_preview={"action": "sell"},
        rpc_health={"ok": True},
        client_order_id="coid_test",
        request_fingerprint="req_test",
        policy=ConfirmationRetryPolicy(max_attempts=3, timeout_seconds=1),
        elapsed_seconds=1.0,
        simulated_outcome="retry",
    )

    assert workflow["final_decision"] == "timeout"
    assert workflow["retry_policy_decision"]["decision"] == "timeout"
    assert workflow["lifecycle_events"][-1]["status"] == "timeout"
    assert workflow["reconciliation"]["final_status"] == "timeout"


def test_build_execution_preview_workflow_retry_then_confirmed_sequence():
    workflow = build_execution_preview_workflow(
        action="buy",
        order_preview={"action": "buy"},
        rpc_health={"ok": True},
        client_order_id="coid_test",
        request_fingerprint="req_test",
        policy=ConfirmationRetryPolicy(max_attempts=3, timeout_seconds=10),
        confirmation_outcomes=["pending", "confirmed"],
    )

    assert workflow["final_decision"] == "confirmed"
    assert workflow["reconciliation"]["attempts_used"] == 2
    assert workflow["lifecycle_events"][-1]["status"] == "confirmed"


def test_build_execution_preview_workflow_rejects_invalid_simulated_outcome():
    with pytest.raises(ValueError):
        build_execution_preview_workflow(
            action="buy",
            order_preview={},
            rpc_health={},
            client_order_id="coid_test",
            request_fingerprint="req_test",
            simulated_outcome="unknown",
        )


def test_build_execution_preview_workflow_adds_submit_confirm_summary():
    workflow = build_execution_preview_workflow(
        action="buy",
        order_preview={"action": "buy"},
        rpc_health={"ok": True},
        client_order_id="coid_test",
        request_fingerprint="req_test",
        simulated_outcome="confirmed",
        confirmation_elapsed_seconds_by_attempt=[0.2],
        simulated_submit_latency_ms=150,
        quote_age_ms_at_submit=50,
        max_quote_age_ms_before_submit=200,
    )
    summary = workflow["submit_confirm_summary"]
    assert summary["outcome_class"] == "submit_confirm_confirmed"
    assert summary["simulated_submit_latency_ms"] == 150
    assert summary["confirmation_attempts_used"] == 1


def test_build_execution_preview_workflow_can_stale_reject_before_confirmation():
    workflow = build_execution_preview_workflow(
        action="buy",
        order_preview={"action": "buy"},
        rpc_health={"ok": True},
        client_order_id="coid_test",
        request_fingerprint="req_test",
        quote_age_ms_at_submit=500,
        max_quote_age_ms_before_submit=100,
    )
    assert workflow["final_decision"] == "stale_quote_reject"
    assert workflow["submit_confirm_summary"]["outcome_class"] == "submit_rejected_stale_quote"
    assert workflow["reconciliation"]["attempts_used"] == 0


def test_build_execution_preview_workflow_can_attach_chain_reconciliation():
    workflow = build_execution_preview_workflow(
        action="buy",
        order_preview={"action": "buy"},
        rpc_health={"ok": True},
        client_order_id="coid_test",
        request_fingerprint="req_test",
        simulated_outcome="confirmed",
        signature_status_payload={"value": [{"slot": 10, "confirmationStatus": "finalized", "err": None}]},
        tx_payload={
            "result": {
                "slot": 10,
                "transaction": {"signatures": ["SIG1"]},
                "meta": {
                    "fee": 5000,
                    "err": None,
                    "preBalances": [10000],
                    "postBalances": [5000],
                    "preTokenBalances": [],
                    "postTokenBalances": [
                        {"accountIndex": 0, "mint": "MINT_A", "owner": "OWNER1", "uiTokenAmount": {"amount": "10"}}
                    ],
                },
            }
        },
    )
    assert workflow["chain_reconciliation"]["outcome_class"] == "live_confirmed_reconciled"
    assert workflow["chain_reconciliation"]["terminal_reason"] == "finalized"
