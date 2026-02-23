import json

import pytest

from src.runner import paper_sim_preset_batch_runner


def test_parse_seeds_parses_csv_string():
    assert paper_sim_preset_batch_runner._parse_seeds("1, 2,3") == [1, 2, 3]


def test_load_presets_from_json_accepts_list(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(
        json.dumps(
            [
                {"name": "custom_a", "usd_size": 100.0},
                {"usd_size": 50.0},
            ]
        ),
        encoding="utf-8",
    )

    presets = paper_sim_preset_batch_runner.load_presets_from_json(str(path))

    assert len(presets) == 2
    assert presets[0]["name"] == "custom_a"
    assert presets[1]["name"] == "preset_2"


def test_load_presets_from_json_accepts_wrapped_object(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"presets": [{"name": "wrapped"}]}), encoding="utf-8")

    presets = paper_sim_preset_batch_runner.load_presets_from_json(str(path))

    assert len(presets) == 1
    assert presets[0]["name"] == "wrapped"


def test_load_presets_from_json_rejects_empty(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"presets": []}), encoding="utf-8")

    with pytest.raises(ValueError):
        paper_sim_preset_batch_runner.load_presets_from_json(str(path))
