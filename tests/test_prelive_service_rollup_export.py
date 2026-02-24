import csv
import json

from src.live.prelive_service_rollup_export import (
    build_service_rollup_export_csv_path,
    build_service_rollup_export_json_path,
    save_service_rollup_csv,
    save_service_rollup_json,
)


def _payload():
    return {
        "loop_name": "prelive",
        "iteration": 5,
        "candidate_preset_name": "candidate_x",
        "policy_profile_name": "default_open",
        "rollup": {"iterations": 5, "signals_seen": 3, "sell_failed": 1},
    }


def test_rollup_export_path_builders_are_deterministic():
    ts = "2026-02-23T20:00:00+00:00"
    j = build_service_rollup_export_json_path("data/exports", timestamp_utc=ts)
    c = build_service_rollup_export_csv_path("data/exports", timestamp_utc=ts)
    assert j.endswith(".json")
    assert c.endswith(".csv")
    assert "2026-02-23T20-00-00_plus_00-00" in j


def test_save_service_rollup_json(tmp_path):
    out = tmp_path / "rollup.json"
    save_service_rollup_json(_payload(), str(out))
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["iteration"] == 5
    assert loaded["rollup"]["signals_seen"] == 3


def test_save_service_rollup_csv(tmp_path):
    out = tmp_path / "rollup.csv"
    save_service_rollup_csv(_payload(), str(out))
    with out.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["loop_name"] == "prelive"
    assert rows[0]["signals_seen"] == "3"
