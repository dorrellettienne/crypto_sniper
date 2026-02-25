from src.live.interfaces import TradeSignal
from src.live.mechanical_safety_filter import (
    JupiterQuoteMechanicalChecker,
    MechanicalSafetyFilter,
)
from src.live.solana_mint_safety import MintSafetyResult, check_mint_safety_detailed


def test_mechanical_safety_filter_rejects_active_mint_authority():
    f = MechanicalSafetyFilter()
    assessment = f.assess(
        TradeSignal(
            token_address="MINT",
            symbol="X",
            entry_price=0.01,
            usd_size=100,
            metadata={"mint_authority_enabled": True, "buy_route_exists": True},
        )
    )
    assert assessment.allowed is False
    assert assessment.primary_reason == "mint_authority_enabled"


def test_mechanical_safety_filter_rejects_no_buy_route():
    f = MechanicalSafetyFilter()
    assessment = f.assess(
        TradeSignal(
            token_address="MINT",
            symbol="X",
            entry_price=0.01,
            usd_size=100,
            metadata={"buy_route_exists": False},
        )
    )
    assert assessment.allowed is False
    assert assessment.primary_reason == "no_buy_route"


def test_mechanical_safety_filter_rejects_price_impact_above_threshold():
    f = MechanicalSafetyFilter(max_buy_price_impact_pct=10.0)
    assessment = f.assess(
        TradeSignal(
            token_address="MINT",
            symbol="X",
            entry_price=0.01,
            usd_size=100,
            metadata={"buy_route_exists": True, "buy_price_impact_pct": 12.5},
        )
    )
    assert assessment.allowed is False
    assert "buy_price_impact_above_max" in (assessment.reasons or [])


def test_mechanical_safety_filter_allows_when_checks_pass():
    f = MechanicalSafetyFilter(max_buy_price_impact_pct=10.0)
    assessment = f.assess(
        TradeSignal(
            token_address="MINT",
            symbol="X",
            entry_price=0.01,
            usd_size=100,
            metadata={
                "mint_authority_enabled": False,
                "freeze_authority_enabled": False,
                "buy_route_exists": True,
                "buy_price_impact_pct": 2.5,
            },
        )
    )
    assert assessment.allowed is True
    assert assessment.primary_reason == ""


def test_check_mint_safety_detailed_uses_rpc_client_authorities():
    class FakeRpc:
        def get_parsed_mint_authorities(self, mint_address: str):
            return {
                "mint_authority": None,
                "freeze_authority": None,
                "supply": "1000",
                "decimals": 6,
            }

    result = check_mint_safety_detailed(rpc_client=FakeRpc(), mint_address="MINT")
    assert result.allowed is True
    assert result.details["mint_address"] == "MINT"


def test_check_mint_safety_detailed_rejects_freeze_authority():
    class FakeRpc:
        def get_parsed_mint_authorities(self, mint_address: str):
            return {
                "mint_authority": None,
                "freeze_authority": "FREEZE",
                "supply": "1000",
                "decimals": 6,
            }

    result = check_mint_safety_detailed(rpc_client=FakeRpc(), mint_address="MINT")
    assert result.allowed is False
    assert result.reason == "freeze_authority_enabled"


def test_jupiter_quote_mechanical_checker_extracts_route_and_price_impact():
    class FakeDex:
        def get_quote_preview(self, **kwargs):
            return {
                "provider": "quote_only_dex",
                "route_count": 1,
                "out_amount": "123",
                "raw_quote": {"outAmount": "123", "priceImpactPct": "4.2"},
            }

    checker = JupiterQuoteMechanicalChecker(FakeDex())
    out = checker(TradeSignal(token_address="MINT", symbol="X", entry_price=0.01, usd_size=50))
    assert out["buy_route_exists"] is True
    assert out["buy_price_impact_pct"] == 4.2


def test_jupiter_quote_mechanical_checker_can_check_sell_route_with_reverse_quote():
    calls = []

    class FakeDex:
        def get_quote_preview(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {
                    "provider": "quote_only_dex",
                    "route_count": 1,
                    "out_amount": "2500000",
                    "raw_quote": {"outAmount": "2500000", "priceImpactPct": "2.0"},
                }
            return {
                "provider": "quote_only_dex",
                "route_count": 1,
                "out_amount": "990000",
                "raw_quote": {"outAmount": "990000", "priceImpactPct": "1.0"},
            }

    checker = JupiterQuoteMechanicalChecker(FakeDex(), check_sell_route=True)
    out = checker(TradeSignal(token_address="MINT", symbol="X", entry_price=0.01, usd_size=50))
    assert out["buy_route_exists"] is True
    assert out["sell_route_exists"] is True
    assert len(calls) == 2
    assert calls[1]["input_mint"] == "MINT"


def test_mechanical_safety_filter_rejects_no_sell_route_when_required():
    f = MechanicalSafetyFilter(require_sell_route=True)
    assessment = f.assess(
        TradeSignal(
            token_address="MINT",
            symbol="X",
            entry_price=0.01,
            usd_size=100,
            metadata={"buy_route_exists": True, "sell_route_exists": False},
        )
    )
    assert assessment.allowed is False
    assert "no_sell_route" in (assessment.reasons or [])


def test_mechanical_safety_filter_rejects_low_liquidity():
    f = MechanicalSafetyFilter(min_buy_liquidity_usd=5000)
    assessment = f.assess(
        TradeSignal(
            token_address="MINT",
            symbol="X",
            entry_price=0.01,
            usd_size=100,
            metadata={"buy_route_exists": True, "liquidity_usd": 1200},
        )
    )
    assert assessment.allowed is False
    assert "liquidity_below_min" in (assessment.reasons or [])


def test_mechanical_safety_filter_fail_closed_rejects_missing_liquidity_signal_when_threshold_enabled():
    f = MechanicalSafetyFilter(min_buy_liquidity_usd=1000, fail_closed_on_check_error=True)
    assessment = f.assess(TradeSignal(token_address="MINT", symbol="X", entry_price=0.01, usd_size=100))
    assert assessment.allowed is False
    assert "missing_liquidity_signal" in (assessment.reasons or [])


def test_mechanical_safety_filter_rejects_missing_sanity_probe_route_when_required():
    f = MechanicalSafetyFilter(require_sanity_probe_route=True)
    assessment = f.assess(
        TradeSignal(
            token_address="MINT",
            symbol="X",
            entry_price=0.01,
            usd_size=100,
            metadata={"buy_route_exists": True, "sanity_buy_route_exists": False},
        )
    )
    assert assessment.allowed is False
    assert "no_buy_route_sanity_probe" in (assessment.reasons or [])


def test_mechanical_safety_filter_uses_mint_checker_and_quote_checker():
    f = MechanicalSafetyFilter(
        mint_safety_checker=lambda mint: MintSafetyResult(True, "", {"mint_address": mint}),
        quote_checker=lambda signal: {"buy_route_exists": True, "buy_price_impact_pct": 1.0},
        max_buy_price_impact_pct=5.0,
    )
    assessment = f.assess(TradeSignal(token_address="MINT", symbol="X", entry_price=0.01, usd_size=100))
    assert assessment.allowed is True
    assert assessment.details["mint_safety"]["allowed"] is True


def test_jupiter_quote_mechanical_checker_can_run_optional_sanity_probe():
    calls = []

    class FakeDex:
        def get_quote_preview(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {"provider": "quote_only_dex", "route_count": 1, "raw_quote": {"outAmount": "500000", "priceImpactPct": "3.1"}}
            return {"provider": "quote_only_dex", "route_count": 1, "raw_quote": {"outAmount": "100000", "priceImpactPct": "1.2"}}

    checker = JupiterQuoteMechanicalChecker(FakeDex(), sanity_probe_usd_size=5.0)
    out = checker(TradeSignal(token_address="MINT", symbol="X", entry_price=0.01, usd_size=50))
    assert out["sanity_buy_route_exists"] is True
    assert out["sanity_route_count"] == 1
    assert len(calls) == 2


def test_jupiter_quote_mechanical_checker_flags_stale_quote_when_age_exceeds_max():
    class FakeDex:
        def get_quote_preview(self, **kwargs):
            return {
                "provider": "quote_only_dex",
                "route_count": 1,
                "fetched_at_unix_ms": 1000,
                "_reliability": {"attempts": 2, "retry_events": 1, "error_classification": "timeout"},
                "raw_quote": {"outAmount": "500000", "priceImpactPct": "2.0"},
            }

    checker = JupiterQuoteMechanicalChecker(FakeDex(), max_quote_age_ms=100, now_ms_fn=lambda: 2000)
    out = checker(TradeSignal(token_address="MINT", symbol="X", entry_price=0.01, usd_size=50))
    assert out["quote_stale_or_invalid"] is True
    assert out["telemetry"]["quote_retry_events"] == 1


def test_mechanical_safety_filter_rejects_stale_quote_and_tracks_telemetry():
    f = MechanicalSafetyFilter(
        quote_checker=lambda signal: {
            "buy_route_exists": True,
            "quote_stale_or_invalid": True,
            "telemetry": {"quote_retry_events": 2, "quote_attempts": 3, "quote_error_classification": "timeout"},
        }
    )
    assessment = f.assess(TradeSignal(token_address="MINT", symbol="X", entry_price=0.01, usd_size=100))
    assert assessment.allowed is False
    assert "quote_stale_or_invalid" in (assessment.reasons or [])
    assert assessment.details["telemetry"]["quote_retry_events"] == 2
