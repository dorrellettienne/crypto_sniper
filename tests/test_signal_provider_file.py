import json

from src.live.signal_provider_file import FileSignalProvider


def test_file_signal_provider_loads_json_array(tmp_path):
    path = tmp_path / "signals.json"
    path.write_text(
        json.dumps(
            [
                {"token_address": "A", "symbol": "AAA", "entry_price": 0.01, "usd_size": 10},
                {"token_address": "B", "symbol": "BBB", "entry_price": 0.02, "usd_size": 20},
            ]
        ),
        encoding="utf-8",
    )

    provider = FileSignalProvider.from_path(str(path))
    s1 = provider.get_next_signal()
    s2 = provider.get_next_signal()
    s3 = provider.get_next_signal()

    assert s1 is not None and s1.token_address == "A"
    assert s2 is not None and s2.symbol == "BBB"
    assert s3 is None


def test_file_signal_provider_loads_json_wrapper_payload(tmp_path):
    path = tmp_path / "signals.json"
    path.write_text(
        json.dumps(
            {
                "signals": [
                    {"token_address": "X", "symbol": "X", "entry_price": 0.03, "usd_size": 30, "metadata": {"src": "test"}}
                ]
            }
        ),
        encoding="utf-8",
    )

    provider = FileSignalProvider.from_path(str(path))
    s = provider.get_next_signal()
    assert s is not None
    assert s.metadata == {"src": "test"}


def test_file_signal_provider_loads_jsonl(tmp_path):
    path = tmp_path / "signals.jsonl"
    path.write_text(
        '\n'.join(
            [
                json.dumps({"token_address": "A1", "symbol": "A1", "entry_price": 0.01, "usd_size": 5}),
                json.dumps({"token_address": "A2", "symbol": "A2", "entry_price": 0.02, "usd_size": 15}),
            ]
        ),
        encoding="utf-8",
    )

    provider = FileSignalProvider.from_path(str(path))
    assert provider.get_next_signal().token_address == "A1"
    assert provider.get_next_signal().token_address == "A2"
    assert provider.get_next_signal() is None


def test_file_signal_provider_rejects_invalid_row(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"symbol": "MISS", "entry_price": 0.01, "usd_size": 10}]), encoding="utf-8")

    try:
        FileSignalProvider.from_path(str(path))
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "token_address" in str(exc)


def test_file_signal_provider_rejects_file_over_size_limit(tmp_path):
    path = tmp_path / "large.jsonl"
    path.write_text('{"token_address":"A","symbol":"A","entry_price":0.01,"usd_size":1}\n', encoding="utf-8")

    try:
        FileSignalProvider.from_path(str(path), max_file_bytes=10)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "max_file_bytes" in str(exc)


def test_file_signal_provider_loads_json_with_utf8_bom(tmp_path):
    path = tmp_path / "signals_bom.json"
    raw = '\ufeff' + json.dumps([{"token_address": "BOM", "symbol": "BOM", "entry_price": 1.0, "usd_size": 1.0}])
    path.write_text(raw, encoding="utf-8")
    provider = FileSignalProvider.from_path(str(path))
    sig = provider.get_next_signal()
    assert sig is not None
    assert sig.token_address == "BOM"
