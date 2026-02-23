import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_audit_log_path(output_dir: str, prefix: str = "execution_audit", timestamp_utc: str | None = None) -> str:
    if timestamp_utc is None:
        timestamp_utc = datetime.now(timezone.utc).isoformat()
    safe_timestamp = str(timestamp_utc).replace(":", "-").replace(".", "-").replace("+", "_plus_")
    return str(Path(output_dir) / f"{prefix}_{safe_timestamp}.jsonl")


def append_audit_event(output_path: str, event_type: str, payload: dict[str, Any]) -> str:
    """
    Appends one structured JSONL audit event and returns the path.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event_type": event_type,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")
    return str(path)
