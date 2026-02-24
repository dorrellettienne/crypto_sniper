import pytest

from src.live.order_lifecycle import (
    ORDER_STATUS_CONFIRMED,
    ORDER_STATUS_FAILED,
    ORDER_STATUS_RETRYING,
    ORDER_STATUS_SUBMITTED,
    ORDER_STATUS_TIMEOUT,
    OrderLifecycleEvent,
    lifecycle_event_to_dict,
    make_submitted,
    mark_confirmed,
    mark_failed,
    mark_retrying,
    mark_timeout,
)


def test_make_submitted_creates_valid_event():
    evt = make_submitted("buy", "ord_1", metadata={"token": "A"})
    assert evt.status == ORDER_STATUS_SUBMITTED
    assert evt.attempt == 1
    assert evt.metadata["token"] == "A"
    assert evt.timestamp_utc


def test_mark_retrying_increments_attempt_and_preserves_order_id():
    evt1 = make_submitted("sell", "ord_2", attempt=1)
    evt2 = mark_retrying(evt1, message="retry", metadata={"reason": "timeout"})

    assert evt2.status == ORDER_STATUS_RETRYING
    assert evt2.attempt == 2
    assert evt2.order_id == "ord_2"
    assert evt2.metadata["reason"] == "timeout"


def test_mark_confirmed_failed_timeout_transitions():
    base = make_submitted("buy", "ord_3", metadata={"a": 1})
    confirmed = mark_confirmed(base, metadata={"txid": "abc"})
    failed = mark_failed(base, message="rpc failed")
    timeout = mark_timeout(base, message="timed out")

    assert confirmed.status == ORDER_STATUS_CONFIRMED
    assert confirmed.metadata["txid"] == "abc"
    assert failed.status == ORDER_STATUS_FAILED
    assert "rpc failed" in failed.message
    assert timeout.status == ORDER_STATUS_TIMEOUT


def test_lifecycle_event_to_dict_serializes_expected_fields():
    evt = make_submitted("buy", "ord_4", metadata={"x": 1})
    payload = lifecycle_event_to_dict(evt)
    assert payload["action"] == "buy"
    assert payload["status"] == ORDER_STATUS_SUBMITTED
    assert payload["order_id"] == "ord_4"
    assert payload["metadata"]["x"] == 1


def test_order_lifecycle_event_rejects_invalid_status_and_attempt():
    with pytest.raises(ValueError):
        OrderLifecycleEvent(action="buy", status="unknown", order_id="ord_x")
    with pytest.raises(ValueError):
        OrderLifecycleEvent(action="buy", status=ORDER_STATUS_SUBMITTED, order_id="ord_x", attempt=0)
