import json

from src.live.live_execution_preview_export import (
    run_live_execution_preview_export,
    serialize_execution_result_preview,
)


class _FakeResult:
    def __init__(self):
        self.ok = False
        self.action = "buy"
        self.position_id = None
        self.pnl = None
        self.message = "x"
        self.metadata = {"k": "v"}


def test_serialize_execution_result_preview_basic_shape():
    payload = serialize_execution_result_preview(_FakeResult())
    assert payload["action"] == "buy"
    assert payload["ok"] is False
    assert payload["metadata"]["k"] == "v"


def test_run_live_execution_preview_export_writes_json_and_jsonl(tmp_path):
    out = run_live_execution_preview_export(
        output_json_dir=str(tmp_path),
        audit_log_dir=str(tmp_path),
    )

    assert out["preview_json_path"] is not None
    assert out["audit_log_path"] is not None

    preview_path = tmp_path / next(p.name for p in tmp_path.iterdir() if p.suffix == ".json")
    payload = json.loads(preview_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "live_execution_preview_skeleton"
    assert "buy" in payload and "sell" in payload and "stop_loss" in payload
    assert payload["buy"]["metadata"]["submit_preview"]["mode"] == "submit_skeleton"

    jsonl_path = tmp_path / next(p.name for p in tmp_path.iterdir() if p.suffix == ".jsonl")
    lines = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    event_types = [line["event_type"] for line in lines]
    assert "live_execution_preview_run_started" in event_types
    assert "live_execution_preview" in event_types
    assert "live_submit_preview" in event_types
    assert "live_confirmation_preview" in event_types
    assert "live_execution_preview_run_completed" in event_types

