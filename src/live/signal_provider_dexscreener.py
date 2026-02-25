from datetime import datetime, timezone
from typing import Any

from src.live.interfaces import TradeSignal
from src.live.signal_provider_polling import PollingSignalProvider


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def parse_dexscreener_pairs_to_signals(
    payload: dict[str, Any] | None,
    default_usd_size: float = 100.0,
    chain_id: str | None = None,
    min_liquidity_usd: float | None = None,
    max_pair_age_seconds: float | None = None,
    now_ts: float | None = None,
) -> list[TradeSignal]:
    """
    Parses a DexScreener-style pairs payload into TradeSignal objects.
    No network calls; pure payload transform for dry-run/pre-live integration.
    """
    if payload is None:
        return []
    if not isinstance(payload, dict):
        raise ValueError("dexscreener payload must be an object")
    pairs = payload.get("pairs", [])
    if not isinstance(pairs, list):
        raise ValueError("dexscreener payload 'pairs' must be a list")

    if now_ts is None:
        now_ts = _now_ts()

    out: list[TradeSignal] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        if chain_id and str(pair.get("chainId") or "").strip().lower() != str(chain_id).strip().lower():
            continue

        base = pair.get("baseToken") or {}
        token_address = str(base.get("address") or "").strip()
        symbol = str(base.get("symbol") or "").strip()
        if not token_address or not symbol:
            continue

        try:
            entry_price = float(pair.get("priceUsd"))
        except Exception:
            continue
        if entry_price <= 0:
            continue

        liquidity_usd = None
        liquidity = pair.get("liquidity")
        if isinstance(liquidity, dict) and liquidity.get("usd") is not None:
            try:
                liquidity_usd = float(liquidity.get("usd"))
            except Exception:
                liquidity_usd = None

        if min_liquidity_usd is not None:
            if liquidity_usd is None or liquidity_usd < float(min_liquidity_usd):
                continue

        token_age_seconds = None
        pair_created_at = pair.get("pairCreatedAt")
        if pair_created_at is not None:
            try:
                # DexScreener commonly returns ms epoch.
                created_ts = float(pair_created_at) / 1000.0
                token_age_seconds = max(0.0, now_ts - created_ts)
            except Exception:
                token_age_seconds = None

        if max_pair_age_seconds is not None and token_age_seconds is not None and token_age_seconds > float(max_pair_age_seconds):
            continue

        out.append(
            TradeSignal(
                token_address=token_address,
                symbol=symbol,
                entry_price=entry_price,
                usd_size=float(default_usd_size),
                metadata={
                    "source": "dexscreener",
                    "chain_id": pair.get("chainId"),
                    "pair_address": pair.get("pairAddress"),
                    "liquidity_usd": liquidity_usd,
                    "token_age_seconds": token_age_seconds,
                },
            )
        )
    return out


class DexScreenerSignalProvider(PollingSignalProvider):
    """
    Polling provider wrapper for DexScreener-style feeds using an injected fetcher.
    Fetcher should return a DexScreener-compatible pairs payload.
    """

    def __init__(
        self,
        fetcher,
        default_usd_size: float = 100.0,
        chain_id: str | None = None,
        min_liquidity_usd: float | None = None,
        max_pair_age_seconds: float | None = None,
        swallow_fetch_errors: bool = True,
        now_ts_fn=None,
    ):
        self._default_usd_size = float(default_usd_size)
        self._chain_id = chain_id
        self._min_liquidity_usd = min_liquidity_usd
        self._max_pair_age_seconds = max_pair_age_seconds
        self._now_ts_fn = now_ts_fn or _now_ts
        self._last_fetch_meta: dict[str, Any] = {}
        self._runtime_counters = {
            "fetch_retry_events": 0,
            "fetch_stale_payload_events": 0,
            "fetch_transport_errors": 0,
        }
        self._last_fetch_meta_index = 0

        def adapted_fetcher():
            payload = fetcher()
            if isinstance(payload, dict):
                meta = payload.get("_fetch_meta")
                if isinstance(meta, dict):
                    self._last_fetch_meta = dict(meta)
                    self._runtime_counters["fetch_retry_events"] += int(meta.get("retry_events", 0) or 0)
                    if bool(meta.get("stale_payload", False)):
                        self._runtime_counters["fetch_stale_payload_events"] += 1
            return parse_dexscreener_pairs_to_signals(
                payload,
                default_usd_size=self._default_usd_size,
                chain_id=self._chain_id,
                min_liquidity_usd=self._min_liquidity_usd,
                max_pair_age_seconds=self._max_pair_age_seconds,
                now_ts=self._now_ts_fn(),
            )

        super().__init__(adapted_fetcher, swallow_fetch_errors=swallow_fetch_errors)

    def poll(self) -> int:
        before_errors = self.fetch_errors
        count = super().poll()
        if self.fetch_errors > before_errors:
            self._runtime_counters["fetch_transport_errors"] += int(self.fetch_errors - before_errors)
        return count

    def consume_runtime_metrics_delta(self) -> dict[str, Any]:
        current = dict(self._runtime_counters)
        last = getattr(self, "_runtime_counters_snapshot", {"fetch_retry_events": 0, "fetch_stale_payload_events": 0, "fetch_transport_errors": 0})
        delta = {
            "fetch_retry_events": int(current["fetch_retry_events"] - last.get("fetch_retry_events", 0)),
            "fetch_stale_payload_events": int(current["fetch_stale_payload_events"] - last.get("fetch_stale_payload_events", 0)),
            "fetch_transport_errors": int(current["fetch_transport_errors"] - last.get("fetch_transport_errors", 0)),
            "last_fetch_meta": dict(self._last_fetch_meta or {}),
            "last_error": str(self.last_error or ""),
        }
        self._runtime_counters_snapshot = current
        return delta
