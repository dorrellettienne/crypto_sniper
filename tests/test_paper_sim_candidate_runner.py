from src.runner import paper_sim_candidate_runner


def test_get_candidate_preset_returns_named_preset(monkeypatch):
    monkeypatch.setattr(
        paper_sim_candidate_runner,
        "load_candidate_presets",
        lambda path: [{"name": "a"}, {"name": "b", "usd_size": 100}],
    )

    preset = paper_sim_candidate_runner.get_candidate_preset("b", "x.json")

    assert preset["name"] == "b"


def test_get_candidate_preset_raises_for_missing(monkeypatch):
    monkeypatch.setattr(
        paper_sim_candidate_runner,
        "load_candidate_presets",
        lambda path: [{"name": "a"}],
    )

    try:
        paper_sim_candidate_runner.get_candidate_preset("missing", "x.json")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "preset not found" in str(exc)


def test_run_candidate_preset_passes_preset_params_and_returns_paths(monkeypatch):
    monkeypatch.setattr(
        paper_sim_candidate_runner,
        "get_candidate_preset",
        lambda preset_name, presets_path: {
            "name": "candidate_x",
            "usd_size": 100.0,
            "stop_loss_percent": 0.12,
            "sell_price": 0.034,
            "p_buy": 0.28,
            "p_stop_loss": 0.15,
            "p_sell": 0.32,
            "p_stop_check": 0.15,
            "p_time_exit": 0.1,
        },
    )
    captured = {}

    def fake_run_simulation(**kwargs):
        captured.update(kwargs)
        return {
            "steps": kwargs["steps"],
            "seed": kwargs["seed"],
            "actions_taken": 1,
            "generated_at_utc": "2026-02-23T00:00:00+00:00",
            "summary": {"total_trades": 1, "total_pnl": 1.0, "wins": 1, "losses": 0, "win_rate": 1.0},
        }

    monkeypatch.setattr(paper_sim_candidate_runner, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(paper_sim_candidate_runner, "build_simulation_summary_export_path", lambda *a, **k: "a.json")
    monkeypatch.setattr(paper_sim_candidate_runner, "build_simulation_summary_export_csv_path", lambda *a, **k: "a.csv")
    monkeypatch.setattr(paper_sim_candidate_runner, "build_closed_trades_export_csv_path", lambda *a, **k: "a_trades.csv")
    monkeypatch.setattr(paper_sim_candidate_runner, "save_simulation_summary_json", lambda result, path: path)
    monkeypatch.setattr(paper_sim_candidate_runner, "save_simulation_summary_csv", lambda result, path: path)
    monkeypatch.setattr(paper_sim_candidate_runner, "save_closed_trades_csv", lambda path: path)

    out = paper_sim_candidate_runner.run_candidate_preset(
        preset_name="candidate_x",
        steps=200,
        seed=7,
        export_json_dir="data/exports",
        export_csv_dir="data/exports",
        export_trades_csv_dir="data/exports",
    )

    assert captured["sell_price"] == 0.034
    assert captured["stop_loss_percent"] == 0.12
    assert captured["p_sell"] == 0.32
    assert out["export_json_path"] == "a.json"
    assert out["export_csv_path"] == "a.csv"
    assert out["export_trades_csv_path"] == "a_trades.csv"


def test_run_candidate_preset_can_skip_exports(monkeypatch):
    monkeypatch.setattr(
        paper_sim_candidate_runner,
        "get_candidate_preset",
        lambda preset_name, presets_path: {
            "name": "candidate_x",
            "usd_size": 100.0,
            "stop_loss_percent": 0.12,
            "sell_price": 0.034,
            "p_buy": 0.28,
            "p_stop_loss": 0.15,
            "p_sell": 0.32,
            "p_stop_check": 0.15,
            "p_time_exit": 0.1,
        },
    )
    monkeypatch.setattr(
        paper_sim_candidate_runner,
        "run_simulation",
        lambda **kwargs: {
            "steps": kwargs["steps"],
            "seed": kwargs["seed"],
            "actions_taken": 0,
            "generated_at_utc": "x",
            "summary": {},
        },
    )

    out = paper_sim_candidate_runner.run_candidate_preset(steps=1, seed=1)

    assert out["export_json_path"] is None
    assert out["export_csv_path"] is None
    assert out["export_trades_csv_path"] is None
