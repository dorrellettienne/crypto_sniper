import argparse
import json
from pathlib import Path
from typing import Any

from src.live.dexscreener_transport import DexScreenerHttpPairsFetcher


def _to_float_or_none(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _load_fallback_urls(path: str) -> list[str]:
    p = Path(str(path or "").strip())
    if not str(p):
        return []
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x or "").strip()]


def _build_candidate(pair: dict[str, Any], usd_size: float) -> dict[str, Any] | None:
    if not isinstance(pair, dict):
        return None
    base = pair.get("baseToken")
    if not isinstance(base, dict):
        return None
    token_address = str(base.get("address") or "").strip()
    symbol = str(base.get("symbol") or "").strip()
    entry_price = _to_float_or_none(pair.get("priceUsd"))
    if not token_address or not symbol or entry_price is None or entry_price <= 0:
        return None
    return {
        "token_address": token_address,
        "symbol": symbol,
        "entry_price": float(entry_price),
        "usd_size": float(usd_size),
        "metadata": {
            "source_provider": "dexscreener",
            "quote_probe_status": "unknown",
            "raw_pair": pair,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch DexScreener pairs and export candidate list for scored discovery.")
    ap.add_argument("--fetch-url", required=True)
    ap.add_argument("--fallback-urls-json-path", default="")
    ap.add_argument("--chain-id", default="solana")
    ap.add_argument("--usd-size", type=float, default=0.25)
    ap.add_argument("--fetch-timeout-seconds", type=float, default=8.0)
    ap.add_argument("--fetch-max-attempts", type=int, default=2)
    ap.add_argument("--fetch-retry-backoff-seconds", type=float, default=0.5)
    ap.add_argument("--max-payload-age-ms", type=int, default=30000)
    ap.add_argument("--allow-stale-payloads", action="store_true")
    ap.add_argument("--user-agent", default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
    ap.add_argument("--header", action="append", default=[])
    ap.add_argument("--max-candidates", type=int, default=100)
    ap.add_argument("--output-json", default="data/exports/v16_discovery_candidates.json")
    args = ap.parse_args()

    fallback_urls = _load_fallback_urls(args.fallback_urls_json_path)
    headers: dict[str, str] = {}
    user_agent = str(args.user_agent or "").strip()
    if user_agent:
        headers["User-Agent"] = user_agent
    for item in list(args.header or []):
        s = str(item or "")
        if ":" not in s:
            raise SystemExit("invalid_header_format_expected_NAME:VALUE")
        k, v = s.split(":", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            raise SystemExit("invalid_header_name_empty")
        headers[k] = v

    fetcher = DexScreenerHttpPairsFetcher(
        url=str(args.fetch_url),
        fallback_urls=fallback_urls,
        timeout_seconds=float(args.fetch_timeout_seconds),
        max_attempts=max(1, int(args.fetch_max_attempts)),
        retry_backoff_seconds=float(args.fetch_retry_backoff_seconds),
        max_payload_age_ms=int(args.max_payload_age_ms) if args.max_payload_age_ms is not None else None,
        fail_on_stale_payload=not bool(args.allow_stale_payloads),
        headers=headers,
    )
    payload = fetcher()
    pairs = payload.get("pairs")
    if not isinstance(pairs, list):
        raise SystemExit("dexscreener_pairs_missing")

    chain_id = str(args.chain_id or "").strip().lower()
    out: list[dict[str, Any]] = []
    for pair in pairs:
        if chain_id:
            pair_chain = str((pair or {}).get("chainId") or "").strip().lower()
            if pair_chain != chain_id:
                continue
        cand = _build_candidate(pair, usd_size=float(args.usd_size))
        if cand is not None:
            out.append(cand)
        if len(out) >= int(args.max_candidates):
            break

    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output_json": str(out_path),
                "pairs_total": len(pairs),
                "candidates_total": len(out),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
