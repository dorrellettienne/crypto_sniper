from dataclasses import dataclass
from typing import Any

from src.live.interfaces import TradeSignal


@dataclass
class TokenSafetyDecision:
    allowed: bool
    reason: str = ""
    details: dict[str, Any] | None = None


@dataclass
class TokenSafetyAssessment:
    allowed: bool
    primary_reason: str = ""
    reasons: list[str] | None = None
    score: float = 1.0
    details: dict[str, Any] | None = None


class TokenSafetyFilter:
    """
    Dry-run/pre-live token safety filter using signal metadata and simple lists.
    Intended as a first anti-scam/anti-dump gate before risk/execution.
    """

    def __init__(
        self,
        token_allowlist: list[str] | None = None,
        token_blocklist: list[str] | None = None,
        min_token_age_seconds: float | None = None,
        min_liquidity_usd: float | None = None,
    ):
        self.token_allowlist = {str(v).strip() for v in (token_allowlist or []) if str(v).strip()}
        self.token_blocklist = {str(v).strip() for v in (token_blocklist or []) if str(v).strip()}
        self.min_token_age_seconds = None if min_token_age_seconds is None else float(min_token_age_seconds)
        self.min_liquidity_usd = None if min_liquidity_usd is None else float(min_liquidity_usd)

    def assess(self, signal: TradeSignal) -> TokenSafetyAssessment:
        token = str(signal.token_address)
        reasons: list[str] = []
        details: dict[str, Any] = {"token_address": token}

        if self.token_allowlist and token not in self.token_allowlist:
            reasons.append("token_not_safety_allowlisted")
        if token in self.token_blocklist:
            reasons.append("token_safety_blocklisted")

        metadata = signal.metadata or {}

        if self.min_token_age_seconds is not None:
            age = metadata.get("token_age_seconds")
            if age is None:
                reasons.append("missing_token_age_seconds")
            else:
                try:
                    age = float(age)
                    details["token_age_seconds"] = age
                except Exception:
                    reasons.append("invalid_token_age_seconds")
                    details["token_age_seconds"] = age
                    age = None
                if age is not None and age < self.min_token_age_seconds:
                    reasons.append("token_too_new")
                    details["min_token_age_seconds"] = self.min_token_age_seconds

        if self.min_liquidity_usd is not None:
            liq = metadata.get("liquidity_usd")
            if liq is None:
                reasons.append("missing_liquidity_usd")
            else:
                try:
                    liq = float(liq)
                    details["liquidity_usd"] = liq
                except Exception:
                    reasons.append("invalid_liquidity_usd")
                    details["liquidity_usd"] = liq
                    liq = None
                if liq is not None and liq < self.min_liquidity_usd:
                    reasons.append("liquidity_below_min")
                    details["min_liquidity_usd"] = self.min_liquidity_usd

        allowed = len(reasons) == 0
        # Simple first-pass safety score: 1.0 minus 0.25 per issue (clamped)
        score = max(0.0, round(1.0 - (0.25 * len(reasons)), 4))
        return TokenSafetyAssessment(
            allowed=allowed,
            primary_reason=(reasons[0] if reasons else ""),
            reasons=reasons,
            score=score,
            details=details,
        )

    def evaluate(self, signal: TradeSignal) -> TokenSafetyDecision:
        assessment = self.assess(signal)
        return TokenSafetyDecision(
            allowed=assessment.allowed,
            reason=assessment.primary_reason,
            details=assessment.details,
        )
