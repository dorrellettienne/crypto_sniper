from dataclasses import dataclass
from typing import Any


@dataclass
class MintSafetyResult:
    allowed: bool
    reason: str = ""
    details: dict[str, Any] | None = None


def _normalize_authority_value(value: Any) -> str | None:
    if value in (None, "", False):
        return None
    if isinstance(value, dict):
        candidate = value.get("address") or value.get("pubkey")
        return None if candidate in (None, "") else str(candidate)
    return str(value)


def evaluate_mint_authority_state(
    *,
    mint_authority: Any,
    freeze_authority: Any,
    require_mint_authority_disabled: bool = True,
    require_freeze_authority_disabled: bool = True,
) -> MintSafetyResult:
    mint_authority = _normalize_authority_value(mint_authority)
    freeze_authority = _normalize_authority_value(freeze_authority)
    details = {
        "mint_authority": mint_authority,
        "freeze_authority": freeze_authority,
    }

    if require_mint_authority_disabled and mint_authority is not None:
        return MintSafetyResult(False, "mint_authority_enabled", details)
    if require_freeze_authority_disabled and freeze_authority is not None:
        return MintSafetyResult(False, "freeze_authority_enabled", details)
    return MintSafetyResult(True, "", details)


def check_mint_safety(rpc_client: Any, mint_address: str) -> tuple[bool, str | None]:
    result = check_mint_safety_detailed(rpc_client=rpc_client, mint_address=mint_address)
    return result.allowed, (result.reason or None)


def check_mint_safety_detailed(
    *,
    rpc_client: Any,
    mint_address: str,
    require_mint_authority_disabled: bool = True,
    require_freeze_authority_disabled: bool = True,
) -> MintSafetyResult:
    if rpc_client is None:
        return MintSafetyResult(False, "mint_safety_check_error", {"error": "missing_rpc_client"})

    try:
        authorities = rpc_client.get_parsed_mint_authorities(str(mint_address))
    except Exception as exc:
        return MintSafetyResult(
            False,
            "mint_safety_check_error",
            {"error": str(exc), "mint_address": str(mint_address)},
        )

    result = evaluate_mint_authority_state(
        mint_authority=authorities.get("mint_authority"),
        freeze_authority=authorities.get("freeze_authority"),
        require_mint_authority_disabled=require_mint_authority_disabled,
        require_freeze_authority_disabled=require_freeze_authority_disabled,
    )
    details = dict(result.details or {})
    details["mint_address"] = str(mint_address)
    details["supply"] = authorities.get("supply")
    details["decimals"] = authorities.get("decimals")
    reliability = authorities.get("_reliability")
    if isinstance(reliability, dict):
        details["telemetry"] = {
            "rpc_attempts": int(reliability.get("attempts", 1) or 1),
            "rpc_retry_events": int(reliability.get("retry_events", 0) or 0),
            "rpc_error_classification": str(reliability.get("error_classification") or ""),
            "rpc_final_error": str(reliability.get("final_error") or ""),
        }
    return MintSafetyResult(result.allowed, result.reason, details)
