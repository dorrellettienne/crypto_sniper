import json
import subprocess
import sys


def test_live_execution_preview_export_cli_smoke(tmp_path):
    json_path = tmp_path / "preview.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.live.live_execution_preview_export",
            "--export-json-path",
            str(json_path),
            "--audit-log-dir",
            str(tmp_path),
            "--allow-unsafe-paths",
        ],
        cwd=".",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "live_execution_preview_skeleton"
    assert "buy" in payload
    assert any(p.suffix == ".jsonl" for p in tmp_path.iterdir())


def test_live_execution_preview_export_cli_accepts_manual_submit_flags(tmp_path):
    json_path = tmp_path / "preview_manual.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.live.live_execution_preview_export",
            "--export-json-path",
            str(json_path),
            "--audit-log-dir",
            str(tmp_path),
            "--manual-submit-approval-enabled",
            "--manual-submit-required-token",
            "APPROVE",
            "--manual-submit-provided-token",
            "APPROVE",
            "--manual-submit-mode",
            "buy_only",
            "--live-send-enabled",
            "--allow-unsafe-paths",
        ],
        cwd=".",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    buy_dispatch = payload["buy"]["metadata"]["submit_dispatch"]
    assert buy_dispatch["reason"] == "would_send_network_gated"
    assert buy_dispatch["would_send"]["rpc_method"] == "sendTransaction"
