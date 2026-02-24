from pathlib import Path

from src.live.path_security import ensure_dir_within_base, ensure_path_within_base


def test_ensure_path_within_base_allows_data_exports_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "data" / "exports" / "x.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    assert ensure_path_within_base(str(target), base_dir=str(tmp_path / "data" / "exports")) == str(target)


def test_ensure_path_within_base_rejects_outside_path(tmp_path):
    base = tmp_path / "data" / "exports"
    outside = tmp_path / "outside" / "x.json"
    try:
        ensure_path_within_base(str(outside), base_dir=str(base))
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "under" in str(exc)


def test_ensure_dir_within_base_allows_nested_dir(tmp_path):
    base = tmp_path / "data" / "exports"
    nested = base / "subdir" / "more"
    assert ensure_dir_within_base(str(nested), base_dir=str(base)) == str(nested)
