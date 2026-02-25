import pytest

from src.live.confirmation_reconciliation import (
    extract_transaction_settlement_summary,
    normalize_signature_confirmation_status,
    reconcile_confirmation_attempts,
    reconcile_live_chain_confirmation,
)
from src.live.confirmation_retry_policy import ConfirmationRetryPolicy
from src.live.order_lifecycle import make_submitted


def _submitted():
    return make_submitted(action="buy", order_id="coid_test", metadata={"x": 1})


def test_reconcile_confirmed_path():
    result = reconcile_confirmation_attempts(
        _submitted(),
        policy=ConfirmationRetryPolicy(max_attempts=3, timeout_seconds=10),
        outcomes=["confirmed"],
        elapsed_seconds_by_attempt=[0.1],
    )

    assert result.final_status == "confirmed"
    assert result.final_decision == "confirmed"
    assert result.attempts_used == 1
    assert len(result.lifecycle_events) == 2
    assert result.lifecycle_events[-1]["status"] == "confirmed"


def test_reconcile_retry_then_confirmed_path():
    result = reconcile_confirmation_attempts(
        _submitted(),
        policy=ConfirmationRetryPolicy(max_attempts=3, timeout_seconds=10, backoff_mode="linear", backoff_base_seconds=0.5),
        outcomes=["pending", "confirmed"],
        elapsed_seconds_by_attempt=[0.1, 0.2],
    )

    assert result.final_status == "confirmed"
    assert result.final_decision == "confirmed"
    assert result.attempts_used == 2
    assert [e["status"] for e in result.lifecycle_events] == ["submitted", "retrying", "confirmed"]
    assert result.retry_decisions[0]["decision"] == "retry"


def test_reconcile_timeout_path():
    result = reconcile_confirmation_attempts(
        _submitted(),
        policy=ConfirmationRetryPolicy(max_attempts=3, timeout_seconds=1),
        outcomes=["pending"],
        elapsed_seconds_by_attempt=[1.0],
    )

    assert result.final_status == "timeout"
    assert result.final_decision == "timeout"
    assert result.lifecycle_events[-1]["status"] == "timeout"


def test_reconcile_fail_after_max_attempts():
    result = reconcile_confirmation_attempts(
        _submitted(),
        policy=ConfirmationRetryPolicy(max_attempts=1, timeout_seconds=10),
        outcomes=["error"],
        elapsed_seconds_by_attempt=[0.1],
    )

    assert result.final_status == "failed"
    assert result.final_decision == "fail"
    assert result.lifecycle_events[-1]["status"] == "failed"


def test_reconcile_rejects_invalid_outcome():
    with pytest.raises(ValueError):
        reconcile_confirmation_attempts(
            _submitted(),
            policy=ConfirmationRetryPolicy(),
            outcomes=["wat"],
        )


def test_normalize_signature_confirmation_status_finalized():
    out = normalize_signature_confirmation_status(
        {
            "result": {
                "value": [
                    {
                        "slot": 123,
                        "confirmationStatus": "finalized",
                        "confirmations": None,
                        "err": None,
                    }
                ]
            }
        }
    )
    assert out["normalized_status"] == "finalized"
    assert out["terminal"] is True
    assert out["retryable"] is False
    assert out["slot"] == 123


def test_normalize_signature_confirmation_status_expired_blockhash():
    out = normalize_signature_confirmation_status(
        {
            "value": [
                {
                    "slot": 999,
                    "err": {"message": "Blockhash not found"},
                }
            ]
        }
    )
    assert out["normalized_status"] == "expired_blockhash"
    assert out["terminal"] is True
    assert out["retryable"] is False


def test_extract_transaction_settlement_summary_parses_fee_and_token_deltas():
    tx = {
        "result": {
            "slot": 555,
            "transaction": {"signatures": ["SIG123"]},
            "meta": {
                "fee": 5000,
                "err": None,
                "preBalances": [10_000, 20_000],
                "postBalances": [4_000, 21_000],
                "preTokenBalances": [
                    {
                        "accountIndex": 0,
                        "mint": "MINT_A",
                        "owner": "OWNER1",
                        "uiTokenAmount": {"amount": "100"},
                    }
                ],
                "postTokenBalances": [
                    {
                        "accountIndex": 0,
                        "mint": "MINT_A",
                        "owner": "OWNER1",
                        "uiTokenAmount": {"amount": "140"},
                    }
                ],
            },
        }
    }
    out = extract_transaction_settlement_summary(tx, owner_filter="OWNER1")
    assert out["tx_present"] is True
    assert out["slot"] == 555
    assert out["signature"] == "SIG123"
    assert out["fee_lamports"] == 5000
    assert out["net_sol_delta_lamports_total"] == -5000
    assert out["token_deltas_by_mint"]["MINT_A"] == 40


def test_reconcile_live_chain_confirmation_detects_mismatch_and_fee_deviation():
    workflow = {
        "final_decision": "confirmed",
        "submit_confirm_summary": {"outcome_class": "submit_confirm_confirmed"},
    }
    status_payload = {"value": [None]}
    tx_payload = None
    out = reconcile_live_chain_confirmation(
        workflow=workflow,
        signature_status_payload=status_payload,
        tx_payload=tx_payload,
        preview_estimates={"network_fee_usd": 0.1},
        mismatch_thresholds={"usd_to_lamports": 10_000_000, "max_fee_deviation_pct": 10},
    )
    assert out["outcome_class"] == "live_confirmation_inconclusive"
    assert "workflow_confirmed_but_chain_not_confirmed" in out["mismatch_flags"]


def test_reconcile_live_chain_confirmation_confirmed_with_tx_is_reconciled():
    workflow = {"final_decision": "confirmed", "submit_confirm_summary": {"outcome_class": "submit_confirm_confirmed"}}
    status_payload = {"value": [{"slot": 7, "confirmationStatus": "confirmed", "err": None}]}
    tx_payload = {
        "result": {
            "slot": 7,
            "transaction": {"signatures": ["SIGX"]},
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
                        "uiTokenAmount": {"amount": "25"},
                    }
                ],
            },
        }
    }
    out = reconcile_live_chain_confirmation(
        workflow=workflow,
        signature_status_payload=status_payload,
        tx_payload=tx_payload,
        owner_filter="OWNER1",
    )
    assert out["outcome_class"] == "live_confirmed_reconciled"
    assert out["terminal_reason"] == "confirmed"
    assert out["mismatch_flags"] == []
