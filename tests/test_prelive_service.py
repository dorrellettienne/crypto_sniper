import argparse

from src.live.interfaces import TradeSignal
from src.live.mechanical_safety_filter import MechanicalSafetyAssessment
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
        dexscreener_fetch_url="",
        dexscreener_fetch_timeout_seconds=5.0,
        dexscreener_fetch_max_attempts=1,
        dexscreener_fetch_retry_backoff_seconds=0.0,
        dexscreener_max_payload_age_ms=None,
        dexscreener_allow_stale_payloads=False,
        dexscreener_chain_id="solana",
        dexscreener_min_liquidity_usd=1234.0,
        dexscreener_max_pair_age_seconds=60.0,
    )

    provider = prelive_service._build_signal_provider_from_args(args, dexscreener_fetcher=lambda: {"pairs": []})

    assert isinstance(provider, FakeProvider)
    assert captured["chain_id"] == "solana"
    assert captured["min_liquidity_usd"] == 1234.0
    assert captured["max_pair_age_seconds"] == 60.0


def test_build_signal_provider_from_args_uses_networked_dexscreener_fetcher(monkeypatch):
    captured = {}

    class FakeFetcher:
        def __init__(self, **kwargs):
            captured["fetcher_kwargs"] = kwargs
        def __call__(self):
            return {"pairs": []}

    class FakeProvider:
        def __init__(self, fetcher, **kwargs):
            captured["provider_fetcher"] = fetcher
            captured["provider_kwargs"] = kwargs

    monkeypatch.setattr(prelive_service, "DexScreenerHttpPairsFetcher", FakeFetcher)
    monkeypatch.setattr(prelive_service, "DexScreenerSignalProvider", FakeProvider)
    args = argparse.Namespace(
        max_iterations=5,
        signals_file_path="",
        use_stub_signals=False,
        use_dexscreener_signals=True,
        dexscreener_fetch_url="https://api.dexscreener.test/pairs",
        dexscreener_fetch_timeout_seconds=4.0,
        dexscreener_fetch_max_attempts=2,
        dexscreener_fetch_retry_backoff_seconds=0.1,
        dexscreener_max_payload_age_ms=1500,
        dexscreener_allow_stale_payloads=True,
        dexscreener_chain_id="solana",
        dexscreener_min_liquidity_usd=1000.0,
        dexscreener_max_pair_age_seconds=60.0,
    )
    provider = prelive_service._build_signal_provider_from_args(args)
    assert isinstance(provider, FakeProvider)
    assert captured["fetcher_kwargs"]["url"] == "https://api.dexscreener.test/pairs"
    assert captured["fetcher_kwargs"]["max_attempts"] == 2
    assert captured["fetcher_kwargs"]["fail_on_stale_payload"] is False


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


def test_prelive_service_tracks_dexscreener_transport_metrics(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda path, event_type, payload: events.append((event_type, payload)))
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 3})()},
    )
    monkeypatch.setattr(prelive_service, "execute_sell_with_retry", lambda **kwargs: {"ok": True, "attempts": 1})

    class Provider:
        def __init__(self):
            self._n = 0

        def get_next_signal(self):
            self._n += 1
            if self._n == 1:
                return TradeSignal(token_address="T", symbol="T", entry_price=0.01, usd_size=100)
            return None

        def consume_runtime_metrics_delta(self):
            if self._n == 1:
                return {"fetch_retry_events": 2, "fetch_transport_errors": 1, "fetch_stale_payload_events": 1, "last_fetch_meta": {}, "last_error": "timeout"}
            return {"fetch_retry_events": 0, "fetch_transport_errors": 0, "fetch_stale_payload_events": 0, "last_fetch_meta": {}, "last_error": ""}

    out = prelive_service.run_prelive_service_loop(signal_provider=Provider(), max_iterations=1)
    assert out["rollup"]["signal_fetch_retry_events"] == 2
    assert out["rollup"]["signal_fetch_errors"] == 1
    assert out["rollup"]["signal_payload_stale_events"] == 1
    assert any(e[0] == "signal_source_transport_status" for e in events)


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


def test_prelive_service_blocks_mechanically_unsafe_signal_before_buy(monkeypatch):
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

    class FakeMechanicalFilter:
        def describe(self):
            return {"enabled": True, "test": True}

        def assess(self, signal):
            return MechanicalSafetyAssessment(
                allowed=False,
                primary_reason="no_buy_route",
                reasons=["no_buy_route"],
                details={"buy_route_exists": False},
            )

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="M", symbol="M", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
        mechanical_safety_filter=FakeMechanicalFilter(),
    )

    assert len(buy_calls) == 0
    assert out["rollup"]["mechanical_blocked"] == 1
    assert out["rollup"]["mechanical_allowed"] == 0
    assert out["rollup"]["mechanical_blocked_by_reason"]["no_buy_route"] == 1
    event_types = [e[0] for e in events]
    assert "mechanical_safety_decision" in event_types


def test_prelive_service_tracks_mechanical_reliability_telemetry(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    events = []
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda path, event_type, payload: events.append((event_type, payload)))

    class FakeMechanicalFilter:
        def describe(self):
            return {"enabled": True}

        def assess(self, signal):
            return MechanicalSafetyAssessment(
                allowed=False,
                primary_reason="quote_stale_or_invalid",
                reasons=["quote_stale_or_invalid"],
                details={
                    "telemetry": {
                        "quote_retry_events": 2,
                        "rpc_retry_events": 1,
                        "mechanical_check_errors": 1,
                        "quote_stale_blocked": 1,
                        "quote_attempts": 3,
                        "rpc_attempts": 2,
                        "quote_error_classification": "timeout",
                        "rpc_error_classification": "transient_network",
                    }
                },
            )

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="M", symbol="M", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
        mechanical_safety_filter=FakeMechanicalFilter(),
    )

    assert out["rollup"]["mechanical_check_errors"] == 1
    assert out["rollup"]["quote_retry_events"] == 2
    assert out["rollup"]["rpc_retry_events"] == 1
    assert out["rollup"]["quote_stale_blocked"] == 1
    event_types = [e[0] for e in events]
    assert "mechanical_quote_retry" in event_types
    assert "mechanical_rpc_retry" in event_types
    assert "mechanical_check_error_classified" in event_types


def test_build_mechanical_safety_filter_from_args_builds_enabled_filter_without_network_clients():
    args = argparse.Namespace(
        enable_mechanical_safety_filter=True,
        use_mechanical_safety_profile=False,
        mechanical_safety_profile_name="strict",
        mechanical_safety_profiles_json_path="config/mechanical_safety_profiles.json",
        mechanical_rpc_url="",
        mechanical_rpc_timeout_seconds=5.0,
        mechanical_rpc_max_attempts=0,
        mechanical_rpc_retry_backoff_seconds=0.0,
        mechanical_quote_url="",
        mechanical_quote_timeout_seconds=5.0,
        mechanical_quote_max_attempts=0,
        mechanical_quote_retry_backoff_seconds=0.0,
        mechanical_quote_input_mint="USDC",
        mechanical_quote_output_mint="USDC",
        mechanical_quote_slippage_bps=50,
        mechanical_require_buy_route=True,
        mechanical_require_sell_route=True,
        mechanical_sanity_probe_usd_size=None,
        mechanical_min_buy_liquidity_usd=None,
        mechanical_max_quote_age_ms=None,
        mechanical_max_buy_price_impact_pct=8.0,
        mechanical_fail_open=False,
    )
    filt = prelive_service._build_mechanical_safety_filter_from_args(args)
    assert filt is not None
    info = filt.describe()
    assert info["require_buy_route"] is True
    assert info["require_sell_route"] is True
    assert info["max_buy_price_impact_pct"] == 8.0


def test_build_mechanical_safety_filter_from_args_returns_none_when_disabled():
    args = argparse.Namespace(enable_mechanical_safety_filter=False)
    assert prelive_service._build_mechanical_safety_filter_from_args(args) is None


def test_build_mechanical_safety_filter_from_args_uses_profile(monkeypatch):
    monkeypatch.setattr(
        prelive_service,
        "get_mechanical_safety_profile",
        lambda **kwargs: {
            "name": "strict",
            "require_buy_route": True,
            "require_sell_route": True,
            "require_sanity_probe_route": True,
            "fail_closed_on_check_error": True,
            "fail_closed_on_quote_error": True,
            "fail_closed_on_rpc_error": True,
            "max_buy_price_impact_pct": 9.0,
            "min_buy_liquidity_usd": 4000.0,
            "sanity_probe_usd_size": 5.0,
            "max_quote_age_ms": 1500,
            "quote_max_attempts": 2,
            "rpc_max_attempts": 2,
            "quote_retry_backoff_seconds": 0.05,
            "rpc_retry_backoff_seconds": 0.05,
        },
    )
    args = argparse.Namespace(
        enable_mechanical_safety_filter=True,
        use_mechanical_safety_profile=True,
        mechanical_safety_profile_name="strict",
        mechanical_safety_profiles_json_path="x.json",
        mechanical_rpc_url="",
        mechanical_rpc_timeout_seconds=5.0,
        mechanical_rpc_max_attempts=0,
        mechanical_rpc_retry_backoff_seconds=0.0,
        mechanical_quote_url="",
        mechanical_quote_timeout_seconds=5.0,
        mechanical_quote_max_attempts=0,
        mechanical_quote_retry_backoff_seconds=0.0,
        mechanical_quote_input_mint="USDC",
        mechanical_quote_output_mint="USDC",
        mechanical_quote_slippage_bps=50,
        mechanical_require_buy_route=False,
        mechanical_require_sell_route=False,
        mechanical_sanity_probe_usd_size=None,
        mechanical_min_buy_liquidity_usd=None,
        mechanical_max_quote_age_ms=None,
        mechanical_max_buy_price_impact_pct=None,
        mechanical_fail_open=True,
    )
    filt = prelive_service._build_mechanical_safety_filter_from_args(args)
    desc = filt.describe()
    assert desc["require_sell_route"] is True
    assert desc["require_sanity_probe_route"] is True
    assert desc["min_buy_liquidity_usd"] == 4000.0
    assert desc["max_buy_price_impact_pct"] == 9.0
    assert desc["fail_closed_on_quote_error"] is True
    assert desc["fail_closed_on_mint_error"] is True


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


def test_prelive_service_blocks_on_volatility_guard_before_buy(monkeypatch):
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

    class FakeVolatilityGuard:
        def describe(self):
            return {"enabled": True}

        def assess(self, **kwargs):
            from src.live.volatility_guard import VolatilityGuardDecision
            return VolatilityGuardDecision(allowed=False, reason="loss_streak_circuit_breaker", details={"current_loss_streak": 3})

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
        volatility_guard=FakeVolatilityGuard(),
    )

    assert len(buy_calls) == 0
    assert out["rollup"]["volatility_guard_blocked"] == 1
    assert out["rollup"]["volatility_guard_blocked_by_reason"]["loss_streak_circuit_breaker"] == 1
    assert any(e[0] == "volatility_guard_decision" for e in events)


def test_prelive_service_derisks_size_via_volatility_guard_before_buy(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)

    buy_calls = []
    monkeypatch.setattr(
        prelive_service,
        "execute_buy_with_controls",
        lambda **kwargs: (buy_calls.append(kwargs) or {"ok": True, "risk_allowed": True, "execution": type("E", (), {"position_id": 1})()}),
    )
    monkeypatch.setattr(prelive_service, "execute_sell_with_retry", lambda **kwargs: {"ok": True, "attempts": 1})

    class FakeVolatilityGuard:
        def describe(self):
            return {"enabled": True}

        def assess(self, **kwargs):
            from src.live.volatility_guard import VolatilityGuardDecision
            return VolatilityGuardDecision(
                allowed=True,
                reason="derisk_applied",
                derisk_applied=True,
                adjusted_usd_size=42.0,
                details={"derisk_reasons": ["loss_streak_derisk"]},
            )

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
        volatility_guard=FakeVolatilityGuard(),
    )

    assert out["rollup"]["volatility_guard_allowed"] == 1
    assert out["rollup"]["volatility_guard_derisked"] == 1
    assert buy_calls[0]["usd_size"] == 42.0


def test_build_volatility_guard_from_args_builds_when_enabled():
    args = argparse.Namespace(
        enable_volatility_guard=True,
        volatility_max_loss_streak_block=3,
        volatility_loss_streak_derisk_threshold=2,
        volatility_max_session_drawdown_usd_block=50.0,
        volatility_session_drawdown_derisk_threshold_usd=25.0,
        volatility_derisk_size_multiplier=0.5,
        volatility_derisk_min_usd_size=20.0,
    )
    g = prelive_service._build_volatility_guard_from_args(args)
    assert g is not None
    d = g.describe()
    assert d["max_loss_streak_block"] == 3
    assert d["derisk_size_multiplier"] == 0.5


def test_build_volatility_guard_from_args_returns_none_when_disabled():
    args = argparse.Namespace(enable_volatility_guard=False)
    assert prelive_service._build_volatility_guard_from_args(args) is None


def test_build_execution_realism_config_from_args():
    args = argparse.Namespace(
        enable_execution_realism=True,
        execution_realism_fill_ratio=0.5,
        execution_realism_latency_ms=200,
        execution_realism_max_quote_age_ms_at_fill=1000,
        execution_realism_expected_slippage_bps=10.0,
        execution_realism_volatility_penalty_bps=5.0,
        execution_realism_latency_penalty_bps_per_100ms=2.0,
        execution_realism_max_realized_slippage_bps=50.0,
    )
    cfg = prelive_service._build_execution_realism_config_from_args(args)
    assert cfg["enabled"] is True
    assert cfg["fill_ratio"] == 0.5
    assert cfg["simulated_latency_ms"] == 200


def test_prelive_service_tracks_execution_realism_partial_fill(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(prelive_service, "execute_sell_with_retry", lambda **kwargs: {"ok": True, "attempts": 1})

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
        execution_realism_config={"enabled": True, "fill_ratio": 0.4},
    )
    assert out["rollup"]["execution_partial_fills"] == 1
    assert out["rollup"]["buy_ok"] == 1


def test_prelive_service_tracks_execution_realism_stale_quote_reject(monkeypatch):
    monkeypatch.setattr(prelive_service, "build_audit_log_path", lambda *a, **k: "audit.jsonl")
    monkeypatch.setattr(prelive_service, "append_audit_event", lambda *a, **k: None)

    out = prelive_service.run_prelive_service_loop(
        signal_provider=StubSignalProvider([TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=100)]),
        max_iterations=1,
        execution_realism_config={"enabled": True, "simulated_latency_ms": 500, "max_quote_age_ms_at_fill": 100},
    )
    assert out["rollup"]["execution_stale_quote_rejects"] == 1
    assert out["rollup"]["buy_failed"] == 1
