from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any


ORDER_STATUS_SUBMITTED = "submitted"
ORDER_STATUS_CONFIRMED = "confirmed"
ORDER_STATUS_FAILED = "failed"
ORDER_STATUS_RETRYING = "retrying"
ORDER_STATUS_TIMEOUT = "timeout"

VALID_ORDER_STATUSES = {
    ORDER_STATUS_SUBMITTED,
    ORDER_STATUS_CONFIRMED,
    ORDER_STATUS_FAILED,
    ORDER_STATUS_RETRYING,
    ORDER_STATUS_TIMEOUT,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OrderLifecycleEvent:
    action: str
    status: str
    order_id: str
    attempt: int = 1
    timestamp_utc: str = ""
    message: str = ""
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        self.action = str(self.action)
        self.status = str(self.status)
        self.order_id = str(self.order_id)
        self.attempt = int(self.attempt)
        if self.status not in VALID_ORDER_STATUSES:
            raise ValueError(f"invalid order lifecycle status: {self.status}")
        if self.attempt <= 0:
            raise ValueError("attempt must be > 0")
        if not self.timestamp_utc:
            self.timestamp_utc = utc_now_iso()
        if self.metadata is None:
            self.metadata = {}


def make_submitted(action: str, order_id: str, attempt: int = 1, message: str = "", metadata: dict[str, Any] | None = None) -> OrderLifecycleEvent:
    return OrderLifecycleEvent(
        action=action,
        status=ORDER_STATUS_SUBMITTED,
        order_id=order_id,
        attempt=attempt,
        message=message,
        metadata=metadata,
    )


def mark_retrying(event: OrderLifecycleEvent, message: str = "", metadata: dict[str, Any] | None = None) -> OrderLifecycleEvent:
    merged = dict(event.metadata or {})
    if metadata:
        merged.update(metadata)
    return replace(
        event,
        status=ORDER_STATUS_RETRYING,
        attempt=event.attempt + 1,
        timestamp_utc=utc_now_iso(),
        message=message or event.message,
        metadata=merged,
    )


def mark_confirmed(event: OrderLifecycleEvent, message: str = "", metadata: dict[str, Any] | None = None) -> OrderLifecycleEvent:
    merged = dict(event.metadata or {})
    if metadata:
        merged.update(metadata)
    return replace(
        event,
        status=ORDER_STATUS_CONFIRMED,
        timestamp_utc=utc_now_iso(),
        message=message or event.message,
        metadata=merged,
    )


def mark_failed(event: OrderLifecycleEvent, message: str = "", metadata: dict[str, Any] | None = None) -> OrderLifecycleEvent:
    merged = dict(event.metadata or {})
    if metadata:
        merged.update(metadata)
    return replace(
        event,
        status=ORDER_STATUS_FAILED,
        timestamp_utc=utc_now_iso(),
        message=message or event.message,
        metadata=merged,
    )


def mark_timeout(event: OrderLifecycleEvent, message: str = "", metadata: dict[str, Any] | None = None) -> OrderLifecycleEvent:
    merged = dict(event.metadata or {})
    if metadata:
        merged.update(metadata)
    return replace(
        event,
        status=ORDER_STATUS_TIMEOUT,
        timestamp_utc=utc_now_iso(),
        message=message or event.message,
        metadata=merged,
    )


def lifecycle_event_to_dict(event: OrderLifecycleEvent) -> dict[str, Any]:
    return {
        "action": event.action,
        "status": event.status,
        "order_id": event.order_id,
        "attempt": event.attempt,
        "timestamp_utc": event.timestamp_utc,
        "message": event.message,
        "metadata": dict(event.metadata or {}),
    }
