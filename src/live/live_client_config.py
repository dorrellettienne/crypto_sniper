def normalize_live_client_config(config: dict | None) -> dict:
    cfg = dict(config or {})
    normalized = dict(cfg)

    normalized["rpc_read_url"] = str(cfg.get("rpc_read_url", cfg.get("rpc_url", "")) or "").strip()
    normalized["dex_quote_url"] = str(cfg.get("dex_quote_url", "" ) or "").strip()
    rpc_timeout = cfg.get("rpc_timeout_seconds", 5.0)
    dex_timeout = cfg.get("dex_quote_timeout_seconds", 5.0)
    normalized["rpc_timeout_seconds"] = float(5.0 if rpc_timeout is None else rpc_timeout)
    normalized["dex_quote_timeout_seconds"] = float(5.0 if dex_timeout is None else dex_timeout)
    normalized["dex_quote_only_mode"] = bool(cfg.get("dex_quote_only_mode", True))
    normalized["use_real_quote_clients"] = bool(cfg.get("use_real_quote_clients", False))

    if normalized["rpc_timeout_seconds"] <= 0:
        raise ValueError("rpc_timeout_seconds must be > 0")
    if normalized["dex_quote_timeout_seconds"] <= 0:
        raise ValueError("dex_quote_timeout_seconds must be > 0")

    return normalized
