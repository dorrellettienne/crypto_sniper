from src.live.interfaces import TradeSignal
from src.live.token_safety_filter import TokenSafetyFilter


def test_token_safety_filter_allows_when_no_rules():
    f = TokenSafetyFilter()
    d = f.evaluate(TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=10))
    assert d.allowed is True


def test_token_safety_filter_blocklist_and_allowlist():
    f = TokenSafetyFilter(token_allowlist=["ALLOW", "BLOCK"], token_blocklist=["BLOCK"])
    assert f.evaluate(TradeSignal(token_address="OTHER", symbol="O", entry_price=0.01, usd_size=10)).reason == "token_not_safety_allowlisted"
    assert f.evaluate(TradeSignal(token_address="BLOCK", symbol="B", entry_price=0.01, usd_size=10)).reason == "token_safety_blocklisted"
    assert f.evaluate(TradeSignal(token_address="ALLOW", symbol="A", entry_price=0.01, usd_size=10)).allowed is True


def test_token_safety_filter_age_checks():
    f = TokenSafetyFilter(min_token_age_seconds=30)
    missing = f.evaluate(TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=10, metadata={}))
    too_new = f.evaluate(TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=10, metadata={"token_age_seconds": 5}))
    ok = f.evaluate(TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=10, metadata={"token_age_seconds": 45}))
    assert missing.reason == "missing_token_age_seconds"
    assert too_new.reason == "token_too_new"
    assert ok.allowed is True


def test_token_safety_filter_liquidity_checks():
    f = TokenSafetyFilter(min_liquidity_usd=1000)
    missing = f.evaluate(TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=10, metadata={}))
    low = f.evaluate(TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=10, metadata={"liquidity_usd": 100}))
    ok = f.evaluate(TradeSignal(token_address="A", symbol="A", entry_price=0.01, usd_size=10, metadata={"liquidity_usd": 5000}))
    assert missing.reason == "missing_liquidity_usd"
    assert low.reason == "liquidity_below_min"
    assert ok.allowed is True


def test_token_safety_filter_assess_collects_multiple_reasons_and_score():
    f = TokenSafetyFilter(
        token_allowlist=["ALLOW", "BLOCK"],
        token_blocklist=["BLOCK"],
        min_token_age_seconds=30,
        min_liquidity_usd=1000,
    )
    assessment = f.assess(
        TradeSignal(
            token_address="BLOCK",
            symbol="B",
            entry_price=0.01,
            usd_size=10,
            metadata={"token_age_seconds": 5, "liquidity_usd": 100},
        )
    )
    assert assessment.allowed is False
    assert assessment.primary_reason == "token_safety_blocklisted"
    assert "token_too_new" in assessment.reasons
    assert "liquidity_below_min" in assessment.reasons
    assert assessment.score < 1.0
