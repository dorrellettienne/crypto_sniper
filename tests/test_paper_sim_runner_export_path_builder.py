from src.runner.paper_sim_runner import build_simulation_summary_export_path


def test_build_export_path_uses_dir_prefix_and_json_extension():
    path = build_simulation_summary_export_path(
        output_dir="data/exports",
        prefix="sim",
        timestamp_utc="2026-02-23T12:34:56+00:00",
    )

    assert path.startswith("data\\exports\\sim_") or path.startswith("data/exports/sim_")
    assert path.endswith(".json")


def test_build_export_path_sanitizes_timestamp_characters():
    path = build_simulation_summary_export_path(
        output_dir="out",
        timestamp_utc="2026-02-23T12:34:56.123456+00:00",
    )

    assert ":" not in path
    assert "_plus_" in path


def test_build_export_path_default_prefix_is_used():
    path = build_simulation_summary_export_path(
        output_dir="out",
        timestamp_utc="2026-02-23T00:00:00+00:00",
    )

    assert "paper_sim_summary_" in path


def test_build_export_path_is_deterministic_for_same_inputs():
    a = build_simulation_summary_export_path(
        output_dir="out",
        prefix="sim",
        timestamp_utc="2026-02-23T00:00:00+00:00",
    )
    b = build_simulation_summary_export_path(
        output_dir="out",
        prefix="sim",
        timestamp_utc="2026-02-23T00:00:00+00:00",
    )

    assert a == b
