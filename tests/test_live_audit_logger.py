import json

from src.live.audit_logger import append_audit_event, build_audit_log_path


def test_build_audit_log_path_uses_jsonl_extension():
    path = build_audit_log_path("data/exports", timestamp_utc="2026-02-23T12:34:56+00:00")
    assert path.endswith(".jsonl")
    assert "execution_audit_2026-02-23T12-34-56_plus_00-00.jsonl" in path


def test_append_audit_event_writes_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_audit_event(str(path), "buy_attempt", {"symbol": "SIM"})
    append_audit_event(str(path), "buy_result", {"ok": True})

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["event_type"] == "buy_attempt"
    assert parsed["payload"]["symbol"] == "SIM"
