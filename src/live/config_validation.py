REQUIRED_PRESET_KEYS = [
    "name",
    "usd_size",
    "stop_loss_percent",
    "sell_price",
    "p_buy",
    "p_stop_loss",
    "p_sell",
    "p_stop_check",
    "p_time_exit",
]


def validate_candidate_preset_config(preset: dict) -> dict:
    """
    Validates a candidate preset dict and returns normalized numeric values.
    Raises ValueError on invalid input.
    """
    missing = [key for key in REQUIRED_PRESET_KEYS if key not in preset]
    if missing:
        raise ValueError(f"missing preset keys: {', '.join(missing)}")

    normalized = dict(preset)
    normalized["name"] = str(normalized["name"])
    if not normalized["name"]:
        raise ValueError("preset name must not be empty")

    for key in ["usd_size", "stop_loss_percent", "sell_price", "p_buy", "p_stop_loss", "p_sell", "p_stop_check", "p_time_exit"]:
        normalized[key] = float(normalized[key])

    if normalized["usd_size"] <= 0:
        raise ValueError("usd_size must be > 0")
    if normalized["stop_loss_percent"] <= 0:
        raise ValueError("stop_loss_percent must be > 0")
    if normalized["sell_price"] <= 0:
        raise ValueError("sell_price must be > 0")

    probs = [normalized["p_buy"], normalized["p_stop_loss"], normalized["p_sell"], normalized["p_stop_check"], normalized["p_time_exit"]]
    if any(p < 0 for p in probs):
        raise ValueError("branch probabilities must be >= 0")
    if abs(sum(probs) - 1.0) > 1e-9:
        raise ValueError("branch probabilities must sum to 1.0")

    return normalized
