from src.live.live_signed_submit_stub_executor import SignedSubmitStubExecutor


class _BaseExec:
    def build_buy_order(self, token_address, symbol, entry_price, usd_size):
        return {"action": "buy", "token_address": token_address}


def test_signed_submit_stub_executor_uses_config_string():
    wrapped = SignedSubmitStubExecutor(_BaseExec(), transaction_base64="ABC_BASE64")
    stub = wrapped.build_signed_submit_stub({"action": "buy"}, "coid1")
    assert stub["ready"] is True
    assert stub["transaction_base64"] == "ABC_BASE64"
    assert stub["source"] == "config_string"
    assert stub["reason"] == ""


def test_signed_submit_stub_executor_reads_file(tmp_path):
    p = tmp_path / "tx.b64"
    p.write_text("FILE_BASE64", encoding="utf-8")
    wrapped = SignedSubmitStubExecutor(_BaseExec(), transaction_base64_path=str(p))
    stub = wrapped.build_signed_submit_stub({"action": "buy"}, "coid2")
    assert stub["ready"] is True
    assert stub["transaction_base64"] == "FILE_BASE64"
    assert stub["source"] == str(p)


def test_signed_submit_stub_executor_reports_missing_source():
    wrapped = SignedSubmitStubExecutor(_BaseExec())
    stub = wrapped.build_signed_submit_stub({"action": "buy"}, "coid3")
    assert stub["ready"] is False
    assert stub["transaction_base64"] is None
    assert stub["reason"] == "signed_submit_stub_missing_transaction_base64"

