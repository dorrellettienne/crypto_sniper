import pytest

from src.live.live_submit_signer import CommandSubmitSigner, StaticSubmitSigner


def test_static_submit_signer_uses_inline_base64():
    signer = StaticSubmitSigner(transaction_base64="ABC123")
    out = signer.build_signed_submit({"action": "buy"}, "coid1", context={"x": 1})
    assert out["ready"] is True
    assert out["transaction_base64"] == "ABC123"
    assert out["source"] == "config_string"
    assert out["context"]["x"] == 1


def test_static_submit_signer_reads_file(tmp_path):
    p = tmp_path / "tx.txt"
    p.write_text("FILE_TX", encoding="utf-8")
    signer = StaticSubmitSigner(transaction_base64_path=str(p))
    out = signer.build_signed_submit({"action": "buy"}, "coid2")
    assert out["ready"] is True
    assert out["transaction_base64"] == "FILE_TX"
    assert out["source"] == str(p)


def test_command_submit_signer_uses_runner_and_normalizes_response():
    seen = {}

    def runner(command, payload, timeout_seconds):
        seen["command"] = list(command)
        seen["payload"] = dict(payload)
        seen["timeout"] = timeout_seconds
        return {"transaction_base64": "CMD_TX", "meta": {"k": 1}}

    signer = CommandSubmitSigner(command=["signer.exe", "--json"], timeout_seconds=3.5, runner=runner)
    out = signer.build_signed_submit({"action": "buy"}, "coid_cmd", context={"a": 1})
    assert out["ready"] is True
    assert out["transaction_base64"] == "CMD_TX"
    assert out["source"] == "command_signer"
    assert out["command"] == ["signer.exe", "--json"]
    assert out["signer_response"]["meta"]["k"] == 1
    assert seen["command"] == ["signer.exe", "--json"]
    assert seen["payload"]["client_order_id"] == "coid_cmd"


def test_command_submit_signer_raises_when_runner_fails():
    def runner(command, payload, timeout_seconds):
        raise RuntimeError("boom")

    signer = CommandSubmitSigner(command=["signer.exe"], runner=runner)
    with pytest.raises(RuntimeError):
        signer.build_signed_submit({"action": "buy"}, "coid_cmd")
