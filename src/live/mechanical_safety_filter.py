from dataclasses import dataclass
import time
from typing import Any, Callable

from src.live.interfaces import TradeSignal
from src.live.solana_mint_safety import MintSafetyResult


@dataclass
class MechanicalSafetyAssessment:
    allowed: bool
    primary_reason: str = ""
    reasons: list[str] | None = None
    details: dict[str, Any] | None = None


class MechanicalSafetyFilter:
    """
    Deterministic hard-reject safety checks inspired by token-sniffer-style screening.
    Solana-focused and bot-native: authority + route + price impact checks.
    """

    def __init__(
        self,
        *,
        mint_safety_checker: Callable[[str], MintSafetyResult] | None = None,
        quote_checker: Callable[[TradeSignal], dict[str, Any]] | None = None,
        require_buy_route: bool = True,
        require_sell_route: bool = False,
        require_sanity_probe_route: bool = False,
        min_buy_liquidity_usd: float | None = None,
        max_buy_price_impact_pct: float | None = None,
        fail_closed_on_check_error: bool = True,
        fail_closed_on_quote_error: bool | None = None,
        fail_closed_on_mint_error: bool | None = None,
    ):
        self.mint_safety_checker = mint_safety_checker
        self.quote_checker = quote_checker
        self.require_buy_route = bool(require_buy_route)
        self.require_sell_route = bool(require_sell_route)
        self.require_sanity_probe_route = bool(require_sanity_probe_route)
        self.min_buy_liquidity_usd = None if min_buy_liquidity_usd is None else float(min_buy_liquidity_usd)
        self.max_buy_price_impact_pct = None if max_buy_price_impact_pct is None else float(max_buy_price_impact_pct)
        self.fail_closed_on_check_error = bool(fail_closed_on_check_error)
        self.fail_closed_on_quote_error = self.fail_closed_on_check_error if fail_closed_on_quote_error is None else bool(fail_closed_on_quote_error)
        self.fail_closed_on_mint_error = self.fail_closed_on_check_error if fail_closed_on_mint_error is None else bool(fail_closed_on_mint_error)
        if self.min_buy_liquidity_usd is not None and self.min_buy_liquidity_usd < 0:
            raise ValueError("min_buy_liquidity_usd must be >= 0")
        if self.max_buy_price_impact_pct is not None and self.max_buy_price_impact_pct < 0:
            raise ValueError("max_buy_price_impact_pct must be >= 0")

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "require_buy_route": self.require_buy_route,
            "require_sell_route": self.require_sell_route,
            "require_sanity_probe_route": self.require_sanity_probe_route,
            "min_buy_liquidity_usd": self.min_buy_liquidity_usd,
            "max_buy_price_impact_pct": self.max_buy_price_impact_pct,
            "fail_closed_on_check_error": self.fail_closed_on_check_error,
            "fail_closed_on_quote_error": self.fail_closed_on_quote_error,
            "fail_closed_on_mint_error": self.fail_closed_on_mint_error,
            "mint_checker": self.mint_safety_checker is not None,
            "quote_checker": self.quote_checker is not None,
        }

    def assess(self, signal: TradeSignal) -> MechanicalSafetyAssessment:
        reasons: list[str] = []
        details: dict[str, Any] = {
            "token_address": str(signal.token_address),
            "symbol": str(signal.symbol),
            "usd_size": float(signal.usd_size),
            "telemetry": {
                "quote_retry_events": 0,
                "rpc_retry_events": 0,
                "mechanical_check_errors": 0,
                "quote_stale_blocked": 0,
            },
        }
        metadata = dict(signal.metadata or {})

        self._apply_mint_checks(signal, metadata, reasons, details)
        self._apply_quote_checks(signal, metadata, reasons, details)

        allowed = len(reasons) == 0
        return MechanicalSafetyAssessment(
            allowed=allowed,
            primary_reason=(reasons[0] if reasons else ""),
            reasons=reasons,
            details=details,
        )

    def _apply_mint_checks(self, signal: TradeSignal, metadata: dict[str, Any], reasons: list[str], details: dict[str, Any]) -> None:
        if self.mint_safety_checker is not None:
            try:
                mint_result = self.mint_safety_checker(str(signal.token_address))
            except Exception as exc:
                details["mint_check_error"] = str(exc)
                details["telemetry"]["mechanical_check_errors"] += 1
                details["telemetry"]["rpc_error_classification"] = "exception"
                if self.fail_closed_on_mint_error:
                    reasons.append("mechanical_mint_check_error")
                return
            details["mint_safety"] = {
                "allowed": bool(mint_result.allowed),
                "reason": mint_result.reason,
                "details": dict(mint_result.details or {}),
            }
            if not mint_result.allowed and mint_result.reason:
                reasons.append(str(mint_result.reason))
            mint_telemetry = (mint_result.details or {}).get("telemetry")
            if isinstance(mint_telemetry, dict):
                details["telemetry"]["rpc_retry_events"] += int(mint_telemetry.get("rpc_retry_events", 0) or 0)
                if mint_telemetry.get("rpc_attempts") is not None:
                    details["telemetry"]["rpc_attempts"] = int(mint_telemetry.get("rpc_attempts", 1) or 1)
                if mint_telemetry.get("rpc_error_classification"):
                    details["telemetry"]["rpc_error_classification"] = str(mint_telemetry.get("rpc_error_classification"))
            return

        # Metadata fallback for paper/pre-live tests and precomputed signal enrichment.
        if bool(metadata.get("mint_authority_enabled", False)):
            reasons.append("mint_authority_enabled")
        if bool(metadata.get("freeze_authority_enabled", False)):
            reasons.append("freeze_authority_enabled")

    def _apply_quote_checks(self, signal: TradeSignal, metadata: dict[str, Any], reasons: list[str], details: dict[str, Any]) -> None:
        quote_info: dict[str, Any] = {}
        if self.quote_checker is not None:
            try:
                quote_info = dict(self.quote_checker(signal) or {})
            except Exception as exc:
                details["quote_check_error"] = str(exc)
                details["telemetry"]["mechanical_check_errors"] += 1
                details["telemetry"]["quote_error_classification"] = "exception"
                if self.fail_closed_on_quote_error:
                    reasons.append("mechanical_quote_check_error")
                return
        else:
            quote_info = self._quote_info_from_metadata(metadata)

        if quote_info:
            details["buy_quote"] = quote_info
        quote_telemetry = quote_info.get("telemetry")
        if isinstance(quote_telemetry, dict):
            details["telemetry"]["quote_retry_events"] += int(quote_telemetry.get("quote_retry_events", 0) or 0)
            if quote_telemetry.get("quote_attempts") is not None:
                details["telemetry"]["quote_attempts"] = int(quote_telemetry.get("quote_attempts", 1) or 1)
            if quote_telemetry.get("quote_error_classification"):
                details["telemetry"]["quote_error_classification"] = str(quote_telemetry.get("quote_error_classification"))

        if bool(quote_info.get("quote_stale_or_invalid", False)):
            details["telemetry"]["quote_stale_blocked"] += 1
            reasons.append("quote_stale_or_invalid")

        if self.require_buy_route:
            route_exists = quote_info.get("buy_route_exists")
            if route_exists is None:
                # If no checker/metadata route signal exists, do not block by default.
                route_exists = True
            if not bool(route_exists):
                reasons.append("no_buy_route")

        if self.require_sell_route:
            sell_route_exists = quote_info.get("sell_route_exists")
            if sell_route_exists is None:
                sell_route_exists = True
            if not bool(sell_route_exists):
                reasons.append("no_sell_route")

        if self.require_sanity_probe_route:
            sanity_route_exists = quote_info.get("sanity_buy_route_exists")
            if sanity_route_exists is None:
                sanity_route_exists = True
            if not bool(sanity_route_exists):
                reasons.append("no_buy_route_sanity_probe")

        if self.min_buy_liquidity_usd is not None:
            liq = quote_info.get("buy_liquidity_usd")
            if liq is None:
                if self.fail_closed_on_quote_error:
                    details["telemetry"]["mechanical_check_errors"] += 1
                    reasons.append("missing_liquidity_signal")
            else:
                try:
                    liq = float(liq)
                except (TypeError, ValueError):
                    if self.fail_closed_on_quote_error:
                        details["telemetry"]["mechanical_check_errors"] += 1
                        reasons.append("missing_liquidity_signal")
                    liq = None
                if liq is not None and liq < self.min_buy_liquidity_usd:
                    details["min_buy_liquidity_usd"] = self.min_buy_liquidity_usd
                    reasons.append("liquidity_below_min")

        if self.max_buy_price_impact_pct is not None:
            price_impact_pct = quote_info.get("buy_price_impact_pct")
            if price_impact_pct is None:
                return
            try:
                price_impact_pct = float(price_impact_pct)
            except (TypeError, ValueError):
                if self.fail_closed_on_quote_error:
                    details["telemetry"]["mechanical_check_errors"] += 1
                    reasons.append("mechanical_quote_check_error")
                return
            if price_impact_pct > self.max_buy_price_impact_pct:
                details["max_buy_price_impact_pct"] = self.max_buy_price_impact_pct
                reasons.append("buy_price_impact_above_max")

    def _quote_info_from_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "buy_route_exists" in metadata:
            out["buy_route_exists"] = bool(metadata.get("buy_route_exists"))
        elif "route_count" in metadata:
            try:
                out["buy_route_exists"] = int(metadata.get("route_count") or 0) > 0
            except Exception:
                pass

        if "buy_price_impact_pct" in metadata:
            out["buy_price_impact_pct"] = metadata.get("buy_price_impact_pct")
        elif "price_impact_pct" in metadata:
            out["buy_price_impact_pct"] = metadata.get("price_impact_pct")

        if "sell_route_exists" in metadata:
            out["sell_route_exists"] = bool(metadata.get("sell_route_exists"))
        if "buy_liquidity_usd" in metadata:
            out["buy_liquidity_usd"] = metadata.get("buy_liquidity_usd")
        elif "liquidity_usd" in metadata:
            out["buy_liquidity_usd"] = metadata.get("liquidity_usd")
        if "sanity_buy_route_exists" in metadata:
            out["sanity_buy_route_exists"] = bool(metadata.get("sanity_buy_route_exists"))
        return out


class JupiterQuoteMechanicalChecker:
    """
    Quote-backed checker for buy-route existence and price-impact gating.
    Uses QuoteOnlyDexExecutor.get_quote_preview (no transaction submission).
    """

    def __init__(
        self,
        dex_quote_executor: Any,
        *,
        input_mint: str = "USDC",
        quote_output_mint: str = "USDC",
        slippage_bps: int = 50,
        check_sell_route: bool = False,
        sanity_probe_usd_size: float | None = None,
        max_quote_age_ms: int | None = None,
        now_ms_fn: Callable[[], int] | None = None,
    ):
        self.dex_quote_executor = dex_quote_executor
        self.input_mint = str(input_mint)
        self.quote_output_mint = str(quote_output_mint)
        self.slippage_bps = int(slippage_bps)
        self.check_sell_route = bool(check_sell_route)
        self.sanity_probe_usd_size = None if sanity_probe_usd_size is None else float(sanity_probe_usd_size)
        self.max_quote_age_ms = None if max_quote_age_ms is None else int(max_quote_age_ms)
        self._now_ms_fn = now_ms_fn or (lambda: int(time.time() * 1000))
        if self.sanity_probe_usd_size is not None and self.sanity_probe_usd_size <= 0:
            raise ValueError("sanity_probe_usd_size must be > 0")
        if self.max_quote_age_ms is not None and self.max_quote_age_ms < 0:
            raise ValueError("max_quote_age_ms must be >= 0")

    def __call__(self, signal: TradeSignal) -> dict[str, Any]:
        amount = max(1, int(round(float(signal.usd_size) * 1_000_000)))
        preview = self.dex_quote_executor.get_quote_preview(
            input_mint=self.input_mint,
            output_mint=str(signal.token_address),
            amount=amount,
            slippage_bps=self.slippage_bps,
        )
        raw_quote = preview.get("raw_quote") if isinstance(preview, dict) else {}
        if not isinstance(raw_quote, dict):
            raw_quote = {}
        route_count = int(preview.get("route_count", 0) or 0)
        out_amount = raw_quote.get("outAmount", preview.get("out_amount"))
        route_exists = route_count > 0 or out_amount not in (None, "", "0", 0)

        price_impact_pct = raw_quote.get("priceImpactPct")
        if price_impact_pct in (None, ""):
            parsed_price_impact = None
        else:
            parsed_price_impact = float(price_impact_pct)
        reliability = preview.get("_reliability") if isinstance(preview, dict) else {}
        if not isinstance(reliability, dict):
            reliability = {}
        quote_age_ms = None
        quote_stale_or_invalid = False
        if self.max_quote_age_ms is not None:
            fetched_at_unix_ms = preview.get("fetched_at_unix_ms")
            try:
                quote_age_ms = max(0, int(self._now_ms_fn()) - int(fetched_at_unix_ms))
            except Exception:
                quote_stale_or_invalid = True
            else:
                if quote_age_ms > self.max_quote_age_ms:
                    quote_stale_or_invalid = True

        sell_route_exists = None
        sell_preview = None
        if self.check_sell_route and route_exists and out_amount not in (None, "", "0", 0):
            sell_preview = self.dex_quote_executor.get_quote_preview(
                input_mint=str(signal.token_address),
                output_mint=self.quote_output_mint,
                amount=max(1, int(out_amount)),
                slippage_bps=self.slippage_bps,
            )
            sell_raw_quote = sell_preview.get("raw_quote") if isinstance(sell_preview, dict) else {}
            if not isinstance(sell_raw_quote, dict):
                sell_raw_quote = {}
            sell_route_count = int(sell_preview.get("route_count", 0) or 0)
            sell_out_amount = sell_raw_quote.get("outAmount", sell_preview.get("out_amount"))
            sell_route_exists = sell_route_count > 0 or sell_out_amount not in (None, "", "0", 0)

        out = {
            "buy_route_exists": bool(route_exists),
            "buy_price_impact_pct": parsed_price_impact,
            "route_count": route_count,
            "out_amount": None if out_amount in (None, "") else str(out_amount),
            "quote_provider": preview.get("provider") if isinstance(preview, dict) else None,
            "quote_stale_or_invalid": bool(quote_stale_or_invalid),
            "telemetry": {
                "quote_attempts": int(reliability.get("attempts", 1) or 1),
                "quote_retry_events": int(reliability.get("retry_events", 0) or 0),
                "quote_error_classification": str(reliability.get("error_classification") or ""),
            },
        }
        if quote_age_ms is not None:
            out["quote_age_ms"] = int(quote_age_ms)
            out["max_quote_age_ms"] = int(self.max_quote_age_ms or 0)
        if isinstance(signal.metadata, dict) and signal.metadata.get("liquidity_usd") is not None:
            out["buy_liquidity_usd"] = signal.metadata.get("liquidity_usd")
        elif raw_quote.get("liquidityUsd") not in (None, ""):
            out["buy_liquidity_usd"] = raw_quote.get("liquidityUsd")
        if sell_route_exists is not None:
            out["sell_route_exists"] = bool(sell_route_exists)
            if isinstance(sell_preview, dict):
                out["sell_route_count"] = int(sell_preview.get("route_count", 0) or 0)
        if self.sanity_probe_usd_size is not None:
            sanity_amount = max(1, int(round(float(self.sanity_probe_usd_size) * 1_000_000)))
            sanity_preview = self.dex_quote_executor.get_quote_preview(
                input_mint=self.input_mint,
                output_mint=str(signal.token_address),
                amount=sanity_amount,
                slippage_bps=self.slippage_bps,
            )
            sanity_raw = sanity_preview.get("raw_quote") if isinstance(sanity_preview, dict) else {}
            if not isinstance(sanity_raw, dict):
                sanity_raw = {}
            sanity_route_count = int(sanity_preview.get("route_count", 0) or 0)
            sanity_out_amount = sanity_raw.get("outAmount", sanity_preview.get("out_amount"))
            out["sanity_buy_route_exists"] = bool(
                sanity_route_count > 0 or sanity_out_amount not in (None, "", "0", 0)
            )
            out["sanity_route_count"] = sanity_route_count
            sanity_rel = sanity_preview.get("_reliability") if isinstance(sanity_preview, dict) else {}
            if isinstance(sanity_rel, dict):
                out["telemetry"]["quote_retry_events"] += int(sanity_rel.get("retry_events", 0) or 0)
                out["telemetry"]["quote_attempts"] += max(0, int(sanity_rel.get("attempts", 1) or 1) - 1)
        return out
