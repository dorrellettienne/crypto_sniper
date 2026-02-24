from src.live.interfaces import ExecutionAdapter, ExecutionResult
from src.live.cost_model import estimate_costs_from_quote_preview
from src.live.live_client_config import normalize_live_client_config
from src.live.live_client_protocols import NoopDexExecutor, NoopRpcClient
from src.live.live_dex_quote_executor import QuoteOnlyDexExecutor
from src.live.live_pilot_safety import build_live_startup_guardrails, evaluate_live_pilot_safety_gate
from src.live.live_rpc_client import HttpRpcClient
from src.live.live_safety_gate import evaluate_live_safety_gate
from src.live.live_execution_workflow import (
    build_execution_preview_workflow,
    build_workflow_identifiers,
)


REQUIRED_LIVE_CONFIG_KEYS = [
    "rpc_url",
    "wallet_public_key",
    "dex_name",
]


def validate_live_execution_config(config: dict | None) -> dict:
    """
    Minimal live execution config validation for Stage 3 adapter scaffolding.
    Live execution remains disabled unless `live_enabled=True`.
    """
    cfg = normalize_live_client_config(config or {})
    live_enabled = bool(cfg.get("live_enabled", False))
    normalized = {"live_enabled": live_enabled}

    for key in REQUIRED_LIVE_CONFIG_KEYS:
        value = cfg.get(key, "")
        normalized[key] = str(value).strip() if value is not None else ""

    # Preserve safety-gate fields for downstream startup enforcement.
    for key in [
        "live_kill_switch",
        "allowlist_tokens",
        "max_order_usd_cap",
        "hard_max_order_usd_cap",
        "pilot_mode",
        "pilot_hard_max_order_usd_cap",
        "pilot_require_single_position",
        "max_concurrent_positions",
        "audit_log_path",
        "candidate_preset_name",
        "use_real_quote_clients",
        "rpc_read_url",
        "dex_quote_url",
        "rpc_timeout_seconds",
        "dex_quote_timeout_seconds",
        "dex_quote_only_mode",
        "live_submit_skeleton_enabled",
        "submit_skeleton_outcome",
        "submit_skeleton_confirmation_outcomes",
    ]:
        if key in cfg:
            normalized[key] = cfg[key]

    if live_enabled:
        missing = [key for key in REQUIRED_LIVE_CONFIG_KEYS if not normalized[key]]
        if missing:
            raise ValueError(f"missing required live execution config keys: {', '.join(missing)}")

    return normalized


class LiveExecutionAdapter(ExecutionAdapter):
    """
    Stage 3 skeleton for a future real execution adapter.
    No network calls or order placement are performed in this implementation.
    """

    def __init__(self, config: dict | None = None, rpc_client=None, dex_executor=None, rpc_transport=None, dex_quote_transport=None):
        self.config = validate_live_execution_config(config)
        self.safety_gate = evaluate_live_safety_gate(self.config)
        if self.live_enabled and not self.safety_gate.allowed:
            raise ValueError(f"live safety gate blocked startup: {self.safety_gate.reason}")
        self.pilot_safety_gate = evaluate_live_pilot_safety_gate(self.config)
        if self.live_enabled and bool(self.config.get("pilot_mode", False)) and not self.pilot_safety_gate.allowed:
            raise ValueError(f"live pilot safety gate blocked startup: {self.pilot_safety_gate.reason}")
        self.startup_guardrails = build_live_startup_guardrails(self.config)
        self.rpc_client = rpc_client
        self.dex_executor = dex_executor
        if self.rpc_client is None or self.dex_executor is None:
            self.rpc_client, self.dex_executor = self._build_default_clients(
                rpc_client=self.rpc_client,
                dex_executor=self.dex_executor,
                rpc_transport=rpc_transport,
                dex_quote_transport=dex_quote_transport,
            )

    def _build_default_clients(self, rpc_client=None, dex_executor=None, rpc_transport=None, dex_quote_transport=None):
        if self.config.get("use_real_quote_clients"):
            rpc = rpc_client if rpc_client is not None else HttpRpcClient(
                rpc_url=self.config.get("rpc_read_url") or self.config.get("rpc_url"),
                timeout_seconds=self.config.get("rpc_timeout_seconds", 5.0),
                transport=rpc_transport,
            )
            dex = dex_executor if dex_executor is not None else QuoteOnlyDexExecutor(
                quote_url=self.config.get("dex_quote_url"),
                timeout_seconds=self.config.get("dex_quote_timeout_seconds", 5.0),
                transport=dex_quote_transport,
                quote_only_mode=self.config.get("dex_quote_only_mode", True),
            )
            return rpc, dex
        return (
            rpc_client if rpc_client is not None else NoopRpcClient(),
            dex_executor if dex_executor is not None else NoopDexExecutor(),
        )

    @property
    def live_enabled(self) -> bool:
        return bool(self.config.get("live_enabled", False))

    def _disabled_result(self, action: str, message: str, metadata: dict | None = None, position_id: int | None = None) -> ExecutionResult:
        return ExecutionResult(
            ok=False,
            action=action,
            position_id=position_id,
            message=message,
            metadata=metadata or {},
        )

    def _submit_skeleton_enabled(self) -> bool:
        return bool(self.config.get("live_submit_skeleton_enabled", False))

    def _submit_skeleton_confirmation_outcomes(self) -> list[str] | None:
        raw = self.config.get("submit_skeleton_confirmation_outcomes")
        if raw is None:
            return None
        if isinstance(raw, str):
            items = [s.strip() for s in raw.split(",") if s.strip()]
            return items or None
        if isinstance(raw, list):
            return [str(v) for v in raw]
        return None

    def _attach_submit_confirm_skeleton(self, action: str, workflow: dict) -> dict:
        enriched = dict(workflow or {})
        if not self._submit_skeleton_enabled():
            return enriched

        client_order_id = str(enriched.get("client_order_id", ""))
        submit_preview = self.dex_executor.build_submit_preview(
            dict(enriched.get("order_preview") or {}),
            client_order_id=client_order_id,
        )
        confirmation_preview = self.rpc_client.build_confirm_preview(client_order_id)

        simulated_outcome = str(self.config.get("submit_skeleton_outcome", "retry") or "retry")
        confirmation_outcomes = self._submit_skeleton_confirmation_outcomes()

        submit_workflow = build_execution_preview_workflow(
            action=action,
            order_preview=dict(enriched.get("order_preview") or {}),
            rpc_health=dict(enriched.get("rpc_health") or {}),
            client_order_id=client_order_id,
            request_fingerprint=str(enriched.get("request_fingerprint", "")),
            simulated_outcome=simulated_outcome,
            confirmation_outcomes=confirmation_outcomes,
        )

        enriched["submit_preview"] = submit_preview
        enriched["confirmation_preview"] = confirmation_preview
        enriched["submit_skeleton_enabled"] = True
        enriched["submit_skeleton_message"] = "submit skeleton only (no transaction sent)"
        enriched["submit_workflow"] = {
            "retry_policy": submit_workflow.get("retry_policy"),
            "retry_policy_decision": submit_workflow.get("retry_policy_decision"),
            "final_decision": submit_workflow.get("final_decision"),
            "lifecycle_events": submit_workflow.get("lifecycle_events"),
            "reconciliation": submit_workflow.get("reconciliation"),
        }
        return enriched

    def _attach_quote_cost_estimate(self, action: str, workflow: dict, fallback_notional_usd: float | None = None) -> dict:
        enriched = dict(workflow or {})
        order_preview = dict(enriched.get("order_preview") or {})
        quote_preview = order_preview.get("quote_preview")
        if quote_preview is None and action != "buy":
            # Sell/stop skeleton previews may have lightweight quote placeholders.
            quote_preview = order_preview.get("quote_preview", {})
        enriched["estimated_costs"] = estimate_costs_from_quote_preview(
            quote_preview,
            fallback_notional_usd=fallback_notional_usd,
        )
        return enriched

    def buy(self, token_address: str, symbol: str, entry_price: float, usd_size: float) -> ExecutionResult:
        metadata = {
            "token_address": token_address,
            "symbol": symbol,
            "entry_price": entry_price,
            "usd_size": usd_size,
        }
        if not self.live_enabled:
            return self._disabled_result("buy", "live execution disabled", metadata=metadata)
        order_preview = self.dex_executor.build_buy_order(token_address, symbol, entry_price, usd_size)
        ids = build_workflow_identifiers(
            action="buy",
            token_address=token_address,
            symbol=symbol,
            entry_price=entry_price,
            usd_size=usd_size,
        )
        workflow = build_execution_preview_workflow(
            action="buy",
            order_preview=order_preview,
            rpc_health=self.rpc_client.health_check(),
            client_order_id=ids["client_order_id"],
            request_fingerprint=ids["request_fingerprint"],
        )
        workflow = self._attach_submit_confirm_skeleton("buy", workflow)
        workflow = self._attach_quote_cost_estimate("buy", workflow, fallback_notional_usd=usd_size)
        return self._disabled_result(
            "buy",
            "live adapter skeleton only (buy not implemented)" if not self._submit_skeleton_enabled() else "live adapter submit skeleton only (buy not implemented)",
            metadata={**metadata, "startup_guardrails": dict(self.startup_guardrails), **workflow},
        )

    def sell(self, position_id: int, exit_price: float) -> ExecutionResult:
        metadata = {"exit_price": exit_price}
        if not self.live_enabled:
            return self._disabled_result("sell", "live execution disabled", metadata=metadata, position_id=position_id)
        order_preview = self.dex_executor.build_sell_order(position_id, exit_price)
        ids = build_workflow_identifiers(action="sell", position_id=position_id, exit_price=exit_price)
        workflow = build_execution_preview_workflow(
            action="sell",
            order_preview=order_preview,
            rpc_health=self.rpc_client.health_check(),
            client_order_id=ids["client_order_id"],
            request_fingerprint=ids["request_fingerprint"],
        )
        workflow = self._attach_submit_confirm_skeleton("sell", workflow)
        workflow = self._attach_quote_cost_estimate("sell", workflow, fallback_notional_usd=None)
        return self._disabled_result(
            "sell",
            "live adapter skeleton only (sell not implemented)" if not self._submit_skeleton_enabled() else "live adapter submit skeleton only (sell not implemented)",
            metadata={**metadata, "startup_guardrails": dict(self.startup_guardrails), **workflow},
            position_id=position_id,
        )

    def stop_loss(self, position_id: int, stop_percent: float) -> ExecutionResult:
        metadata = {"stop_percent": stop_percent}
        if not self.live_enabled:
            return self._disabled_result("stop_loss", "live execution disabled", metadata=metadata, position_id=position_id)
        order_preview = self.dex_executor.build_stop_loss_order(position_id, stop_percent)
        ids = build_workflow_identifiers(action="stop_loss", position_id=position_id, stop_percent=stop_percent)
        workflow = build_execution_preview_workflow(
            action="stop_loss",
            order_preview=order_preview,
            rpc_health=self.rpc_client.health_check(),
            client_order_id=ids["client_order_id"],
            request_fingerprint=ids["request_fingerprint"],
        )
        workflow = self._attach_submit_confirm_skeleton("stop_loss", workflow)
        workflow = self._attach_quote_cost_estimate("stop_loss", workflow, fallback_notional_usd=None)
        return self._disabled_result(
            "stop_loss",
            "live adapter skeleton only (stop loss not implemented)" if not self._submit_skeleton_enabled() else "live adapter submit skeleton only (stop loss not implemented)",
            metadata={**metadata, "startup_guardrails": dict(self.startup_guardrails), **workflow},
            position_id=position_id,
        )
