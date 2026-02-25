import json
import os
import subprocess
import sys


SCRIPT = "examples/solana_command_signer_template.py"


def _run(payload: dict, env_overrides: dict[str, str | None] | None = None) -> tuple[int, dict]:
    env = os.environ.copy()
    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
    proc = subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    out = json.loads((proc.stdout or "").strip() or "{}")
    return proc.returncode, out


def test_solana_command_signer_template_requires_unsigned_tx():
    rc, out = _run({"client_order_id": "c1", "order_preview": {"action": "buy"}, "context": {}})
    assert rc != 0
    assert out["reason"] == "missing_unsigned_transaction_base64"


def test_solana_command_signer_template_requires_secret_env():
    payload = {
        "client_order_id": "c1",
        "order_preview": {"action": "buy"},
        "context": {"unsigned_submit": {"ready": True, "unsigned_transaction_base64": "AAAA"}},
    }
    rc, out = _run(payload, {"SOLANA_PILOT_PRIVATE_KEY_B58": None, "SOLANA_PILOT_KEYPAIR_JSON_PATH": None})
    assert rc != 0
    assert out["reason"] == "missing_signer_secret_env"


def test_solana_command_signer_template_returns_clear_error_without_solders(tmp_path):
    p = tmp_path / "kp.json"
    p.write_text(json.dumps([1] * 64), encoding="utf-8")
    payload = {
        "client_order_id": "c1",
        "order_preview": {"action": "buy"},
        "context": {"unsigned_submit": {"ready": True, "unsigned_transaction_base64": "AAAA"}},
    }
    rc, out = _run(payload, {"SOLANA_PILOT_KEYPAIR_JSON_PATH": str(p), "SOLANA_PILOT_PRIVATE_KEY_B58": None})
    assert rc != 0
    assert out["reason"] in {
        "solders_not_installed",
        "invalid_unsigned_transaction_bytes",
        "invalid_keypair_bytes",
        "solders_signing_failed",
    }
    assert (out.get("meta") or {}).get("template") == "solana_command_signer_template.py"
