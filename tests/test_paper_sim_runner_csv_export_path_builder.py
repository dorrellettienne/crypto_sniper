from src.runner.paper_sim_runner import build_simulation_summary_export_csv_path


def test_build_csv_export_path_uses_csv_extension():
    path = build_simulation_summary_export_csv_path(
        output_dir="data/exports",
        prefix="sim",
        timestamp_utc="2026-02-23T12:34:56+00:00",
    )
    assert path.endswith(".csv")


def test_build_csv_export_path_sanitizes_timestamp():
    path = build_simulation_summary_export_csv_path(
        output_dir="out",
        timestamp_utc="2026-02-23T12:34:56.123456+00:00",
    )
    assert ":" not in path
    assert "_plus_" in path
