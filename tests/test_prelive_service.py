import argparse

from src.live.interfaces import TradeSignal
from src.live.signal_provider_stub import StubSignalProvider
from src.live.signal_provider_dexscreener import DexScreenerSignalProvider
from src.live import prelive_service


def test_prelive_service_no_signals_rollup(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda path, event_type, payload: events.append(event_type))

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([]),
        max_iterations=3,
        audit_log_dir="data/exports",
    )

    assert out["rollup"]["iterations"] == 3
    assert out["rollup"]["signals_seen"] == 0
    assert out["rollup"]["signals_missing"] == 3
    assert events[0] == "service_started"
    assert events[-1] == "service_completed"


def test_prelive_service_risk_blocked_path(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)

    class BlockRisk:
        def can_buy(self, token_address: str, symbol: str, usd_size: float):
            from src.live.interfaces import RiskDecision
            return RiskDecision(allowed=False, reason="blocked")

    monkeypatch.setattr(prelive_service, "PreLiveRiskEngine", lambda **kwargs: BlockRisk())

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="S", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
    )

    assert out["rollup"]["signals_seen"] == 1
    assert out["rollup"]["risk_blocked"] == 1
    assert out["rollup"]["buy_ok"] == 0


def test_prelive_service_allowed_execution_and_retry_path(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)

    class AllowRisk:
        def can_buy(self, token_address: str, symbol: str, usd_size: float):
            from src.live.interfaces import RiskDecision
            return RiskDecision(allowed=True, reason="")

    monkeypatch.setattr(prelive_service, "PreLiveRiskEngine", lambda **kwargs: AllowRisk())
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 7})(), "reason": ""},
    )
    monkeypatch.setattr(
        prelive_service,
        "execute_sell_with_retry",
        lambda **kwargs: {"ok": True, "attempts": 2, "result": None, "errors": ["temp"]},
    )

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="S", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
    )

    assert out["rollup"]["risk_allowed"] == 1
    assert out["rollup"]["buy_ok"] == 1
    assert out["rollup"]["sell_ok"] == 1
    assert out["rollup"]["sell_retry_events"] == 1
    assert out["rollup"]["max_sell_attempts"] == 2


def test_prelive_service_rejects_invalid_iterations():
    try:
        prelive_service.run_prelive_service_loop(signal_provider=StubSignalProvider([]), max_iterations=0)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "max_iterations" in str(exc)


def test_prelive_service_emits_periodic_rollup_and_sleeps(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    captured = []
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda path, event_type, payload: captured.append((event_type, payload)))
    sleeps = []

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([]),
        max_iterations=3,
        rollup_emit_every=2,
        interval_seconds=0.25,
        sleep_fn=lambda seconds: sleeps.append(seconds),
    )

    assert out["rollup"]["iterations"] == 3
    assert sleeps == [0.25, 0.25]
    rollup_events = [evt for evt in captured if evt[0] == "service_rollup"]
    assert len(rollup_events) == 1
    assert rollup_events[0][1]["iteration"] == 2
    assert rollup_events[0][1]["rollup"]["signals_missing"] == 2


def test_prelive_service_candidate_preset_overrides_signal_usd_and_sell_price(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(
        prelive_service,
        "get_candidate_preset",
        lambda **kwargs: {"name": "winner", "usd_size": 321.0, "sell_price": 0.034},
    )

    buy_calls = []
    sell_calls = []
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: (buy_calls.append(kwargs) or {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 9})()}),
    )
    monkeypatch.setattr(
        prelive_service,
        "execute_sell_with_retry",
        lambda **kwargs: (sell_calls.append(kwargs) or {"ok": True, "attempts": 1}),
    )

    prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="S", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
        use_candidate_preset=True,
        candidate_preset_name="winner",
    )

    assert buy_calls[0]["usd_size"] == 321.0
    assert sell_calls[0]["exit_price"] == 0.034


def test_prelive_service_cycle_error_continues_by_default(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda path, event_type, payload: events.append(event_type))
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider(
            [
                TradeSignal(token_address="A", symbol="S", entry_price=0.01, usd_size=100),
                TradeSignal(token_address="B", symbol="S", entry_price=0.01, usd_size=100),
            ]
        ),
        max_iterations=2,
    )

    assert out["rollup"]["service_errors"] == 2
    assert out["rollup"]["iterations"] == 2
    assert "service_error" in events
    assert events[-1] == "service_completed"


def test_prelive_service_cycle_error_can_stop(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda path, event_type, payload: events.append(event_type))
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("stop boom")),
    )

    try:
        prelive_service.run_prelive_service_loop(
            signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="S", entry_price=0.01, usd_size=100)]),
            max_iterations=1,
            continue_on_cycle_error=False,
        )
        assert False, "Expected RuntimeError"
    except RuntimeError as exc:
        assert "stop boom" in str(exc)

    assert "service_error" in events


def test_prelive_service_uses_policy_profile_and_exports_final_rollups(monkeypatch, tmp_path):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(
        prelive_service,
        "get_policy_profile",
        lambda **kwargs: {
            "name": "strict",
            "token_allowlist": ["A"],
            "token_blocklist": [],
            "symbol_allowlist": ["S"],
            "min_usd_size": 50.0,
            "max_usd_size": 150.0,
            "token_cooldown_calls": 2,
        },
    )

    risk_kwargs = {}

    class AllowRisk:
        def can_buy(self, token_address: str, symbol: str, usd_size: float):
            from src.live.interfaces import RiskDecision
            return RiskDecision(allowed=True, reason="")

    def fake_risk_engine(**kwargs):
        risk_kwargs.update(kwargs)
        return AllowRisk()

    monkeypatch.setattr(prelive_service, "PreLiveRiskEngine", fake_risk_engine)
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 2})()},
    )
    monkeypatch.setattr(prelive_service, "execute_sell_with_retry", lambda **kwargs: {"ok": True, "attempts": 1})

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="S", entry_price=0.01, usd_size=10)]),
        max_iterations=1,
        use_policy_profile=True,
        rollup_export_json_dir=str(tmp_path),
        rollup_export_csv_dir=str(tmp_path),
    )

    assert risk_kwargs["min_usd_size"] == 50.0
    assert risk_kwargs["max_usd_size"] == 150.0
    assert risk_kwargs["token_cooldown_calls"] == 2
    assert out["final_rollup_json_path"] is not None
    assert out["final_rollup_csv_path"] is not None


def test_prelive_service_rejects_multiple_signal_sources():
    try:
        prelive_service._validate_service_startup_args(use_stub_signals=True, signals_file_path="signals.json")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "one signal source" in str(exc)


def test_prelive_service_rejects_stub_and_dexscreener_signal_sources():
    try:
        prelive_service._validate_service_startup_args(
            use_stub_signals=True,
            signals_file_path="",
            use_dexscreener_signals=True,
        )
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "one signal source" in str(exc)


def test_build_signal_provider_from_args_builds_dexscreener_provider(monkeypatch):
    captured = {}

    class FakeProvider:
        def __init__(self, fetcher, default_usd_size, chain_id, min_liquidity_usd, max_pair_age_seconds, **kwargs):
            captured["fetcher"] = fetcher
            captured["default_usd_size"] = default_usd_size
            captured["chain_id"] = chain_id
            captured["min_liquidity_usd"] = min_liquidity_usd
            captured["max_pair_age_seconds"] = max_pair_age_seconds

    monkeypatch.setattr(prelive_service, "DexScreenerSignalProvider", FakeProvider)
    args = argparse.Namespace(
        max_iterations=5,
        signals_file_path="",
        use_stub_signals=False,
        use_dexscreener_signals=True,
        dexscreener_chain_id="solana",
        dexscreener_min_liquidity_usd=1234.0,
        dexscreener_max_pair_age_seconds=60.0,
    )

    provider = prelive_service._build_signal_provider_from_args(args, dexscreener_fetcher=lambda: {"pairs": []})

    assert isinstance(provider, FakeProvider)
    assert captured["chain_id"] == "solana"
    assert captured["min_liquidity_usd"] == 1234.0
    assert captured["max_pair_age_seconds"] == 60.0


def test_prelive_service_runs_one_cycle_with_dexscreener_provider(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda path, event_type, payload: events.append((event_type, payload)))
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 3})()},
    )
    monkeypatch.setattr(prelive_service, "execute_sell_with_retry", lambda **kwargs: {"ok": True, "attempts": 1})

    payload = {
        "pairs": [
            {
                "chainId": "solana",
                "pairAddress": "PAIR1",
                "priceUsd": "0.01",
                "pairCreatedAt": 1700000000000,
                "liquidity": {"usd": 15000},
                "baseToken": {"address": "TOKEN1", "symbol": "TK1"},
            }
        ]
    }
    provider = DexScreenerSignalProvider(
        lambda: payload,
        chain_id="solana",
        min_liquidity_usd=1000,
        max_pair_age_seconds=3600,
        now_ts_fn=lambda: 1700000060,
    )

    out = prelive_service.run_prelive_service_loop(
        signal_provider=provider,
        max_iterations=1,
    )

    assert out["rollup"]["signals_seen"] == 1
    assert out["rollup"]["buy_ok"] == 1
    started_payload = next(p for e, p in events if e == "service_started")
    assert started_payload["signal_source"]["provider_class"] == "DexScreenerSignalProvider"


def test_prelive_service_suppresses_duplicate_buy_requests(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda path, event_type, payload: events.append((event_type, payload)))

    buy_calls = []
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: (buy_calls.append(kwargs) or {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 1})()}),
    )
    monkeypatch.setattr(prelive_service, "execute_sell_with_retry", lambda **kwargs: {"ok": True, "attempts": 1})

    signal = TradeSignal(token_address="DUP", symbol="D", entry_price=0.01, usd_size=100)
    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([signal, signal]),
        max_iterations=2,
    )

    assert len(buy_calls) == 1
    assert out["rollup"]["idempotency_suppressed"] == 1
    event_types = [e[0] for e in events]
    assert "client_order_id_assigned" in event_types
    assert "idempotency_duplicate_suppressed" in event_types


def test_prelive_service_blocks_unsafe_signal_before_buy(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda path, event_type, payload: events.append((event_type, payload)))

    buy_calls = []
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: (buy_calls.append(kwargs) or {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 1})()}),
    )
    monkeypatch.setattr(prelive_service, "execute_sell_with_retry", lambda **kwargs: {"ok": True, "attempts": 1})

    unsafe_signal = TradeSignal(
        token_address="UNSAFE",
        symbol="U",
        entry_price=0.01,
        usd_size=100,
        metadata={"token_age_seconds": 2, "liquidity_usd": 100000},
    )
    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([unsafe_signal]),
        max_iterations=1,
        safety_min_token_age_seconds=30,
    )

    assert len(buy_calls) == 0
    assert out["rollup"]["safety_blocked"] == 1
    assert out["rollup"]["risk_allowed"] == 0
    event_types = [e[0] for e in events]
    assert "token_safety_decision" in event_types
    safety_event = next(payload for event_type, payload in events if event_type == "token_safety_decision")
    assert "reasons" in safety_event
    assert "score" in safety_event
    assert safety_event["allowed"] is False


def test_prelive_service_uses_token_safety_profile(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(
        prelive_service,
        "get_token_safety_profile",
        lambda **kwargs: {
            "name": "strict_launch_filter",
            "token_allowlist": [],
            "token_blocklist": [],
            "min_token_age_seconds": 60.0,
            "min_liquidity_usd": 5000.0,
        },
    )

    buy_calls = []
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: (buy_calls.append(kwargs) or {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 1})()}),
    )
    monkeypatch.setattr(prelive_service, "execute_sell_with_retry", lambda **kwargs: {"ok": True, "attempts": 1})

    signal = TradeSignal(
        token_address="X",
        symbol="X",
        entry_price=0.01,
        usd_size=100,
        metadata={"token_age_seconds": 5, "liquidity_usd": 10000},
    )
    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([signal]),
        max_iterations=1,
        use_token_safety_profile=True,
    )

    assert len(buy_calls) == 0
    assert out["rollup"]["safety_blocked"] == 1
    assert out["token_safety_profile"]["name"] == "strict_launch_filter"


def test_prelive_service_file_idempotency_store_persists_duplicate_suppression_across_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)

    buy_calls = []
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: (buy_calls.append(kwargs) or {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 1})()}),
    )
    monkeypatch.setattr(prelive_service, "execute_sell_with_retry", lambda **kwargs: {"ok": True, "attempts": 1})

    signal = TradeSignal(token_address="PERSIST", symbol="P", entry_price=0.01, usd_size=100)
    idem_path = tmp_path / "idem_keys.txt"

    first = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([signal]),
        max_iterations=1,
        idempotency_store_path=str(idem_path),
    )
    second = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([signal]),
        max_iterations=1,
        idempotency_store_path=str(idem_path),
    )

    assert first["rollup"]["idempotency_suppressed"] == 0
    assert second["rollup"]["idempotency_suppressed"] == 1
    assert len(buy_calls) == 1
