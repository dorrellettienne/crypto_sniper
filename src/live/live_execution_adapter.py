from src.live.interfaces import ExecutionAdapter, ExecutionResult
from src.live.cost_model import estimate_costs_from_quote_preview
from src.live.live_client_config import normalize_live_client_config
from src.live.live_client_protocols import NoopDexExecutor, NoopRpcClient
from src.live.live_dex_quote_executor import QuoteOnlyDexExecutor
from src.live.live_signed_submit_stub_executor import SignedSubmitStubExecutor
from src.live.live_submit_signer import CommandSubmitSigner, StaticSubmitSigner
from src.live.live_pilot_safety import build_live_startup_guardrails, evaluate_live_pilot_safety_gate
from src.live.live_rpc_client import HttpRpcClient
from src.live.live_safety_gate import evaluate_live_safety_gate
from src.live.confirmation_reconciliation import reconcile_live_chain_confirmation
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
        "dex_quote_input_mint",
        "dex_swap_url",
        "dex_swap_timeout_seconds",
        "dex_quote_only_mode",
        "live_submit_skeleton_enabled",
        "submit_skeleton_outcome",
        "submit_skeleton_confirmation_outcomes",
        "submit_skeleton_confirmation_elapsed_seconds",
        "submit_skeleton_quote_age_ms_at_submit",
        "submit_skeleton_max_quote_age_ms_before_submit",
        "submit_skeleton_latency_ms",
        "submit_skeleton_signature_status_payload",
        "submit_skeleton_tx_payload",
        "submit_skeleton_chain_reconciliation_thresholds",
        "submit_skeleton_fetch_chain_reconciliation",
        "submit_skeleton_chain_signature",
        "submit_skeleton_chain_owner_filter",
        "manual_submit_approval_enabled",
        "manual_submit_required_token",
        "manual_submit_provided_token",
        "manual_submit_mode",
        "live_send_enabled",
        "live_send_network_enabled",
        "live_send_skip_preflight",
        "live_send_max_retries",
        "live_send_preflight_commitment",
        "live_send_fetch_chain_reconciliation",
        "live_send_chain_owner_filter",
        "live_send_chain_reconciliation_thresholds",
        "live_send_max_orders_per_session",
        "live_send_max_notional_usd_total",
        "live_send_pause_on_reconciliation_mismatch",
        "live_send_pause_on_reconciliation_inconclusive",
        "live_send_pause_reset_required_token",
        "live_send_pause_reset_provided_token",
        "dex_signed_submit_stub_tx_base64",
        "dex_signed_submit_stub_tx_base64_path",
        "submit_signer_static_tx_base64",
        "submit_signer_static_tx_base64_path",
        "submit_signer_command",
        "submit_signer_command_timeout_seconds",
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

    def __init__(self, config: dict | None = None, rpc_client=None, dex_executor=None, rpc_transport=None, dex_quote_transport=None, dex_swap_transport=None, submit_signer=None):
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
        self.submit_signer = submit_signer
        if self.rpc_client is None or self.dex_executor is None:
            self.rpc_client, self.dex_executor = self._build_default_clients(
                rpc_client=self.rpc_client,
                dex_executor=self.dex_executor,
                rpc_transport=rpc_transport,
                dex_quote_transport=dex_quote_transport,
                dex_swap_transport=dex_swap_transport,
            )
        if self.submit_signer is None and (
            self.config.get("submit_signer_static_tx_base64") or self.config.get("submit_signer_static_tx_base64_path")
        ):
            self.submit_signer = StaticSubmitSigner(
                transaction_base64=self.config.get("submit_signer_static_tx_base64"),
                transaction_base64_path=self.config.get("submit_signer_static_tx_base64_path"),
            )
        if self.submit_signer is None and self.config.get("submit_signer_command"):
            raw_cmd = self.config.get("submit_signer_command")
            if isinstance(raw_cmd, str):
                cmd = [part for part in str(raw_cmd).split(" ") if part]
            else:
                cmd = [str(v) for v in list(raw_cmd)]
            self.submit_signer = CommandSubmitSigner(
                command=cmd,
                timeout_seconds=float(self.config.get("submit_signer_command_timeout_seconds", 10.0) or 10.0),
            )
        self._live_send_session_orders_submitted = 0
        self._live_send_session_notional_usd_submitted = 0.0
        self._live_send_pause_latched = False
        self._live_send_pause_reason = ""
        self._live_send_runtime_counters = {
            "submit_dispatch_calls": 0,
            "submit_dispatch_attempted": 0,
            "submit_dispatch_submitted": 0,
            "submit_dispatch_blocked_manual_not_approved": 0,
            "submit_dispatch_blocked_live_send_disabled": 0,
            "submit_dispatch_blocked_network_gated": 0,
            "submit_dispatch_blocked_action_not_enabled": 0,
            "submit_dispatch_blocked_session_order_cap": 0,
            "submit_dispatch_blocked_session_notional_cap": 0,
            "submit_dispatch_paused_latched_blocks": 0,
            "submit_dispatch_reconciliation_mismatch_latches": 0,
            "submit_dispatch_reconciliation_inconclusive_latches": 0,
            "submit_dispatch_pause_reset_events": 0,
        }

    def _build_default_clients(self, rpc_client=None, dex_executor=None, rpc_transport=None, dex_quote_transport=None, dex_swap_transport=None):
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
                quote_input_mint=self.config.get("dex_quote_input_mint"),
                swap_url=self.config.get("dex_swap_url"),
                swap_timeout_seconds=self.config.get("dex_swap_timeout_seconds", self.config.get("dex_quote_timeout_seconds", 5.0)),
                swap_transport=dex_swap_transport,
                swap_user_public_key=self.config.get("wallet_public_key"),
            )
            if self.config.get("dex_signed_submit_stub_tx_base64") or self.config.get("dex_signed_submit_stub_tx_base64_path"):
                dex = SignedSubmitStubExecutor(
                    dex,
                    transaction_base64=self.config.get("dex_signed_submit_stub_tx_base64"),
                    transaction_base64_path=self.config.get("dex_signed_submit_stub_tx_base64_path"),
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

    def _maybe_reset_live_send_pause_latch(self) -> dict | None:
        if not self._live_send_pause_latched:
            return None
        required_token = str(self.config.get("live_send_pause_reset_required_token") or "").strip()
        provided_token = str(self.config.get("live_send_pause_reset_provided_token") or "").strip()
        if required_token and provided_token and required_token == provided_token:
            previous_reason = str(self._live_send_pause_reason or "")
            self._live_send_pause_latched = False
            self._live_send_pause_reason = ""
            self._live_send_runtime_counters["submit_dispatch_pause_reset_events"] += 1
            return {
                "reset_applied": True,
                "reason": "pause_reset_token_matched",
                "previous_pause_reason": previous_reason,
            }
        return {
            "reset_applied": False,
            "reason": "pause_latched_reset_token_missing_or_mismatch",
            "previous_pause_reason": str(self._live_send_pause_reason or ""),
        }

    def _live_send_runtime_snapshot(self) -> dict:
        return {
            **{k: int(v) for k, v in self._live_send_runtime_counters.items()},
            "session_orders_submitted": int(self._live_send_session_orders_submitted),
            "session_notional_usd_submitted": round(float(self._live_send_session_notional_usd_submitted), 6),
            "pause_latched": bool(self._live_send_pause_latched),
            "pause_reason": str(self._live_send_pause_reason or ""),
        }

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

    def _submit_skeleton_confirmation_elapsed_seconds(self) -> list[float] | None:
        raw = self.config.get("submit_skeleton_confirmation_elapsed_seconds")
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            return [float(raw)]
        if isinstance(raw, str):
            items = [s.strip() for s in raw.split(",") if s.strip()]
            return [float(v) for v in items] if items else None
        if isinstance(raw, list):
            return [float(v) for v in raw]
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
        confirmation_elapsed_seconds = self._submit_skeleton_confirmation_elapsed_seconds()
        quote_age_ms_at_submit = self.config.get("submit_skeleton_quote_age_ms_at_submit")
        max_quote_age_ms_before_submit = self.config.get("submit_skeleton_max_quote_age_ms_before_submit")
        simulated_submit_latency_ms = int(self.config.get("submit_skeleton_latency_ms", 0) or 0)
        signature_status_payload = self.config.get("submit_skeleton_signature_status_payload")
        tx_payload = self.config.get("submit_skeleton_tx_payload")
        chain_reconciliation_thresholds = self.config.get("submit_skeleton_chain_reconciliation_thresholds")
        chain_owner_filter = self.config.get("submit_skeleton_chain_owner_filter")

        chain_fetch_meta = None
        if bool(self.config.get("submit_skeleton_fetch_chain_reconciliation", False)):
            chain_signature = str(self.config.get("submit_skeleton_chain_signature") or "").strip()
            if chain_signature:
                chain_fetch_meta = {"enabled": True, "signature": chain_signature, "status_fetched": False, "tx_fetched": False, "error": ""}
                try:
                    if hasattr(self.rpc_client, "get_signature_status"):
                        fetched_status = self.rpc_client.get_signature_status(chain_signature)
                        signature_status_payload = {"value": [fetched_status]}
                        chain_fetch_meta["status_fetched"] = True
                    if hasattr(self.rpc_client, "get_transaction"):
                        fetched_tx = self.rpc_client.get_transaction(chain_signature)
                        tx_payload = {"result": fetched_tx} if fetched_tx is not None else None
                        chain_fetch_meta["tx_fetched"] = fetched_tx is not None
                except Exception as exc:
                    chain_fetch_meta["error"] = str(exc)
            else:
                chain_fetch_meta = {"enabled": True, "signature": "", "status_fetched": False, "tx_fetched": False, "error": "missing_chain_signature"}

        submit_workflow = build_execution_preview_workflow(
            action=action,
            order_preview=dict(enriched.get("order_preview") or {}),
            rpc_health=dict(enriched.get("rpc_health") or {}),
            client_order_id=client_order_id,
            request_fingerprint=str(enriched.get("request_fingerprint", "")),
            simulated_outcome=simulated_outcome,
            confirmation_outcomes=confirmation_outcomes,
            confirmation_elapsed_seconds_by_attempt=confirmation_elapsed_seconds,
            quote_age_ms_at_submit=quote_age_ms_at_submit,
            max_quote_age_ms_before_submit=max_quote_age_ms_before_submit,
            simulated_submit_latency_ms=simulated_submit_latency_ms,
            signature_status_payload=signature_status_payload,
            tx_payload=tx_payload,
            preview_estimates=enriched.get("estimated_costs"),
            chain_reconciliation_thresholds=chain_reconciliation_thresholds,
            chain_reconciliation_owner_filter=None if chain_owner_filter in (None, "") else str(chain_owner_filter),
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
            "submit_confirm_summary": submit_workflow.get("submit_confirm_summary"),
            "chain_reconciliation": submit_workflow.get("chain_reconciliation"),
        }
        if chain_fetch_meta is not None:
            enriched["submit_workflow"]["chain_reconciliation_fetch"] = chain_fetch_meta
        return enriched

    def _attach_manual_submit_scaffold(self, action: str, workflow: dict) -> dict:
        enriched = dict(workflow or {})
        if not self._submit_skeleton_enabled():
            return enriched

        approval_enabled = bool(self.config.get("manual_submit_approval_enabled", False))
        required_token = str(self.config.get("manual_submit_required_token") or "").strip()
        provided_token = str(self.config.get("manual_submit_provided_token") or "").strip()
        manual_mode = str(self.config.get("manual_submit_mode") or "disabled").strip() or "disabled"

        token_match = bool(required_token) and bool(provided_token) and (required_token == provided_token)
        approved = approval_enabled and token_match

        reason = ""
        if manual_mode == "disabled":
            reason = "manual_submit_mode_disabled"
        elif not approval_enabled:
            reason = "manual_approval_not_enabled"
        elif not required_token:
            reason = "missing_required_token"
        elif not provided_token:
            reason = "missing_provided_token"
        elif not token_match:
            reason = "approval_token_mismatch"

        order_preview = dict(enriched.get("order_preview") or {})
        submit_preview = dict(enriched.get("submit_preview") or {})
        estimated_costs = dict(enriched.get("estimated_costs") or {})
        client_order_id = str(enriched.get("client_order_id") or "")
        request_fingerprint = str(enriched.get("request_fingerprint") or "")
        quote_preview = dict(order_preview.get("quote_preview") or {}) if isinstance(order_preview.get("quote_preview"), dict) else {}

        manual_submit_request = {
            "mode": "manual_submit_scaffold_v1",
            "action": str(action),
            "manual_submit_mode": manual_mode,
            "ready_for_manual_submit": approved,
            "approval_required": approval_enabled,
            "approval_granted": approved,
            "approval_reason": "" if approved else reason,
            "client_order_id": client_order_id,
            "request_fingerprint": request_fingerprint,
            "order_action": order_preview.get("action"),
            "submit_preview_mode": submit_preview.get("mode"),
            "quote_age_ms_at_submit": (enriched.get("submit_workflow") or {}).get("submit_confirm_summary", {}).get("quote_age_ms_at_submit"),
            "estimated_notional_usd": estimated_costs.get("notional_usd"),
            "estimated_total_cost_usd": estimated_costs.get("estimated_total_cost_usd"),
            "quote_route_count": quote_preview.get("route_count"),
            "expected_out_amount": quote_preview.get("out_amount"),
            "send_enabled": False,
            "send_reason": "manual_submit_scaffold_only_no_send",
        }

        enriched["manual_submit_gate"] = {
            "enabled": approval_enabled,
            "mode": manual_mode,
            "approved": approved,
            "required_token_present": bool(required_token),
            "provided_token_present": bool(provided_token),
            "token_match": token_match,
            "reason": "" if approved else reason,
        }
        enriched["manual_submit_request"] = manual_submit_request
        enriched["submit_dispatch"] = self._build_submit_dispatch_stub(
            action=action,
            workflow=enriched,
            manual_gate=enriched["manual_submit_gate"],
            manual_request=manual_submit_request,
        )
        return enriched

    def _build_submit_dispatch_stub(self, *, action: str, workflow: dict, manual_gate: dict, manual_request: dict) -> dict:
        self._live_send_runtime_counters["submit_dispatch_calls"] += 1
        live_send_enabled = bool(self.config.get("live_send_enabled", False))
        live_send_network_enabled = bool(self.config.get("live_send_network_enabled", False))
        pause_reset = self._maybe_reset_live_send_pause_latch()
        if not manual_gate.get("approved", False):
            out = {
                "mode": "submit_dispatch_stub_v1",
                "action": str(action),
                "attempted": False,
                "live_send_enabled": live_send_enabled,
                "live_send_network_enabled": live_send_network_enabled,
                "ready": False,
                "reason": "manual_submit_not_approved",
            }
            self._live_send_runtime_counters["submit_dispatch_blocked_manual_not_approved"] += 1
            if pause_reset is not None:
                out["pause_reset"] = pause_reset
            out["runtime_counters"] = self._live_send_runtime_snapshot()
            return out

        if not live_send_enabled:
            out = {
                "mode": "submit_dispatch_stub_v1",
                "action": str(action),
                "attempted": False,
                "live_send_enabled": False,
                "live_send_network_enabled": live_send_network_enabled,
                "ready": False,
                "reason": "live_send_disabled",
            }
            self._live_send_runtime_counters["submit_dispatch_blocked_live_send_disabled"] += 1
            if pause_reset is not None:
                out["pause_reset"] = pause_reset
            out["runtime_counters"] = self._live_send_runtime_snapshot()
            return out

        if self._live_send_pause_latched:
            out = {
                "mode": "submit_dispatch_stub_v1",
                "action": str(action),
                "attempted": False,
                "live_send_enabled": live_send_enabled,
                "live_send_network_enabled": live_send_network_enabled,
                "ready": False,
                "reason": "live_send_paused_latched",
                "pause_latched": True,
                "pause_reason": str(self._live_send_pause_reason or ""),
            }
            self._live_send_runtime_counters["submit_dispatch_paused_latched_blocks"] += 1
            if pause_reset is not None:
                out["pause_reset"] = pause_reset
            out["runtime_counters"] = self._live_send_runtime_snapshot()
            return out

        order_preview = dict(workflow.get("order_preview") or {})
        client_order_id = str(workflow.get("client_order_id") or "")
        requested_notional_usd = None
        try:
            requested_notional_usd = float((manual_request or {}).get("estimated_notional_usd"))
        except (TypeError, ValueError):
            requested_notional_usd = None
        if str(action) != "buy":
            return {
                "mode": "submit_dispatch_stub_v1",
                "action": str(action),
                "attempted": False,
                "live_send_enabled": live_send_enabled,
                "live_send_network_enabled": live_send_network_enabled,
                "ready": False,
                "reason": "live_send_action_not_enabled",
                "runtime_counters": self._live_send_runtime_snapshot(),
            }
        if hasattr(self.dex_executor, "build_submit_request_stub"):
            try:
                request_stub = self.dex_executor.build_submit_request_stub(order_preview, client_order_id)
            except Exception as exc:
                return {
                    "mode": "submit_dispatch_stub_v1",
                    "action": str(action),
                    "attempted": True,
                    "live_send_enabled": True,
                    "live_send_network_enabled": live_send_network_enabled,
                    "ready": False,
                    "reason": "submit_request_stub_error",
                    "error": str(exc),
                    "runtime_counters": self._live_send_runtime_snapshot(),
                }
            if not live_send_network_enabled:
                unsigned_submit_stub = None
                if hasattr(self.dex_executor, "build_unsigned_submit_stub"):
                    try:
                        unsigned_submit_stub = self.dex_executor.build_unsigned_submit_stub(order_preview, client_order_id)
                    except Exception:
                        unsigned_submit_stub = None
                signed_submit_preview = None
                signed_submit_source = ""
                signed_submit_preview_error = None
                if self.submit_signer is not None and hasattr(self.submit_signer, "build_signed_submit"):
                    try:
                        signed_submit_preview = self.submit_signer.build_signed_submit(
                            order_preview,
                            client_order_id,
                            context={
                                "manual_request": {
                                    "client_order_id": manual_request.get("client_order_id"),
                                    "request_fingerprint": manual_request.get("request_fingerprint"),
                                    "estimated_notional_usd": manual_request.get("estimated_notional_usd"),
                                },
                                "unsigned_submit": dict(unsigned_submit_stub or {}),
                            },
                        )
                        signed_submit_source = "submit_signer"
                    except Exception as exc:
                        signed_submit_preview_error = str(exc)
                elif hasattr(self.dex_executor, "build_signed_submit_stub"):
                    try:
                        signed_submit_preview = self.dex_executor.build_signed_submit_stub(order_preview, client_order_id)
                        signed_submit_source = "dex_executor_stub"
                    except Exception as exc:
                        signed_submit_preview_error = str(exc)
                self._live_send_runtime_counters["submit_dispatch_attempted"] += 1
                self._live_send_runtime_counters["submit_dispatch_blocked_network_gated"] += 1
                out = {
                    "mode": "submit_dispatch_stub_v1",
                    "action": str(action),
                    "attempted": True,
                    "live_send_enabled": True,
                    "live_send_network_enabled": False,
                    "ready": False,
                    "reason": "would_send_network_gated",
                    "request_stub": request_stub,
                    "unsigned_submit_stub": unsigned_submit_stub,
                    "signed_submit": signed_submit_preview,
                    "signed_submit_source": signed_submit_source,
                    "would_send": {
                        "rpc_method": "sendTransaction",
                        "client_order_id": client_order_id,
                        "request_stub_mode": request_stub.get("mode"),
                    },
                    "manual_request_ref": {
                        "client_order_id": manual_request.get("client_order_id"),
                        "request_fingerprint": manual_request.get("request_fingerprint"),
                    },
                    **({"signed_submit_preview_error": signed_submit_preview_error} if signed_submit_preview_error else {}),
                    **({"pause_reset": pause_reset} if pause_reset is not None else {}),
                }
                out["runtime_counters"] = self._live_send_runtime_snapshot()
                return out
            cap_orders = self.config.get("live_send_max_orders_per_session")
            cap_notional = self.config.get("live_send_max_notional_usd_total")
            try:
                cap_orders_int = int(cap_orders) if cap_orders not in (None, "") else None
            except (TypeError, ValueError):
                cap_orders_int = None
            try:
                cap_notional_f = float(cap_notional) if cap_notional not in (None, "") else None
            except (TypeError, ValueError):
                cap_notional_f = None

            session_caps = {
                "orders_submitted": int(self._live_send_session_orders_submitted),
                "notional_usd_submitted": round(float(self._live_send_session_notional_usd_submitted), 6),
                "max_orders_per_session": cap_orders_int,
                "max_notional_usd_total": cap_notional_f,
            }
            if cap_orders_int is not None and self._live_send_session_orders_submitted >= cap_orders_int:
                out = {
                    "mode": "submit_dispatch_stub_v1",
                    "action": str(action),
                    "attempted": False,
                    "live_send_enabled": True,
                    "live_send_network_enabled": True,
                    "ready": False,
                    "reason": "live_send_session_order_cap_reached",
                    "request_stub": request_stub,
                    "session_caps": session_caps,
                    **({"pause_reset": pause_reset} if pause_reset is not None else {}),
                }
                self._live_send_runtime_counters["submit_dispatch_blocked_session_order_cap"] += 1
                out["runtime_counters"] = self._live_send_runtime_snapshot()
                return out
            if (
                cap_notional_f is not None
                and requested_notional_usd is not None
                and (self._live_send_session_notional_usd_submitted + requested_notional_usd) > cap_notional_f
            ):
                out = {
                    "mode": "submit_dispatch_stub_v1",
                    "action": str(action),
                    "attempted": False,
                    "live_send_enabled": True,
                    "live_send_network_enabled": True,
                    "ready": False,
                    "reason": "live_send_session_notional_cap_exceeded",
                    "request_stub": request_stub,
                    "session_caps": {
                        **session_caps,
                        "requested_notional_usd": requested_notional_usd,
                    },
                    **({"pause_reset": pause_reset} if pause_reset is not None else {}),
                }
                self._live_send_runtime_counters["submit_dispatch_blocked_session_notional_cap"] += 1
                out["runtime_counters"] = self._live_send_runtime_snapshot()
                return out
            unsigned_submit_stub = None
            if hasattr(self.dex_executor, "build_unsigned_submit_stub"):
                try:
                    unsigned_submit_stub = self.dex_executor.build_unsigned_submit_stub(order_preview, client_order_id)
                except Exception:
                    unsigned_submit_stub = None
            signed_submit = None
            signed_submit_source = ""
            if self.submit_signer is not None and hasattr(self.submit_signer, "build_signed_submit"):
                try:
                    signed_submit = self.submit_signer.build_signed_submit(
                        order_preview,
                        client_order_id,
                        context={
                            "manual_request": {
                                "client_order_id": manual_request.get("client_order_id"),
                                "request_fingerprint": manual_request.get("request_fingerprint"),
                                "estimated_notional_usd": manual_request.get("estimated_notional_usd"),
                            },
                            "unsigned_submit": dict(unsigned_submit_stub or {}),
                        },
                    )
                    signed_submit_source = "submit_signer"
                except Exception as exc:
                    return {
                        "mode": "submit_dispatch_stub_v1",
                        "action": str(action),
                        "attempted": True,
                        "live_send_enabled": True,
                        "live_send_network_enabled": True,
                        "ready": False,
                        "reason": "submit_signer_error",
                        "request_stub": request_stub,
                        "error": str(exc),
                        "runtime_counters": self._live_send_runtime_snapshot(),
                    }
            else:
                if not hasattr(self.dex_executor, "build_signed_submit_stub"):
                    return {
                        "mode": "submit_dispatch_stub_v1",
                        "action": str(action),
                        "attempted": True,
                        "live_send_enabled": True,
                        "live_send_network_enabled": True,
                        "ready": False,
                        "reason": "dex_executor_signed_submit_stub_not_supported",
                        "request_stub": request_stub,
                        "runtime_counters": self._live_send_runtime_snapshot(),
                    }
                try:
                    signed_submit = self.dex_executor.build_signed_submit_stub(order_preview, client_order_id)
                    signed_submit_source = "dex_executor_stub"
                except Exception as exc:
                    return {
                        "mode": "submit_dispatch_stub_v1",
                        "action": str(action),
                        "attempted": True,
                        "live_send_enabled": True,
                        "live_send_network_enabled": True,
                        "ready": False,
                        "reason": "signed_submit_stub_error",
                        "request_stub": request_stub,
                        "error": str(exc),
                        "runtime_counters": self._live_send_runtime_snapshot(),
                    }
            tx_b64 = signed_submit.get("transaction_base64") if isinstance(signed_submit, dict) else None
            if not tx_b64:
                return {
                    "mode": "submit_dispatch_stub_v1",
                    "action": str(action),
                    "attempted": True,
                    "live_send_enabled": True,
                    "live_send_network_enabled": True,
                    "ready": False,
                    "reason": "missing_signed_transaction_base64",
                    "request_stub": request_stub,
                    "unsigned_submit_stub": unsigned_submit_stub,
                    "signed_submit": signed_submit,
                    "signed_submit_source": signed_submit_source,
                    "runtime_counters": self._live_send_runtime_snapshot(),
                }
            if not hasattr(self.rpc_client, "send_raw_transaction"):
                return {
                    "mode": "submit_dispatch_stub_v1",
                    "action": str(action),
                    "attempted": True,
                    "live_send_enabled": True,
                    "live_send_network_enabled": True,
                    "ready": False,
                    "reason": "rpc_send_raw_transaction_not_supported",
                    "request_stub": request_stub,
                    "unsigned_submit_stub": unsigned_submit_stub,
                    "signed_submit": signed_submit,
                    "signed_submit_source": signed_submit_source,
                    "runtime_counters": self._live_send_runtime_snapshot(),
                }
            self._live_send_runtime_counters["submit_dispatch_attempted"] += 1
            try:
                sig = self.rpc_client.send_raw_transaction(
                    str(tx_b64),
                    skip_preflight=bool(self.config.get("live_send_skip_preflight", False)),
                    max_retries=self.config.get("live_send_max_retries"),
                    preflight_commitment=str(self.config.get("live_send_preflight_commitment", "processed")),
                )
            except Exception as exc:
                return {
                    "mode": "submit_dispatch_stub_v1",
                    "action": str(action),
                    "attempted": True,
                    "live_send_enabled": True,
                    "live_send_network_enabled": True,
                    "ready": False,
                    "reason": "send_raw_transaction_error",
                    "request_stub": request_stub,
                    "signed_submit": signed_submit,
                    "error": str(exc),
                    "runtime_counters": self._live_send_runtime_snapshot(),
                }
            dispatch_payload = {
                "mode": "submit_dispatch_stub_v1",
                "action": str(action),
                "attempted": True,
                "live_send_enabled": True,
                "live_send_network_enabled": True,
                "ready": True,
                "reason": "send_raw_transaction_submitted",
                "request_stub": request_stub,
                "unsigned_submit_stub": unsigned_submit_stub,
                "signed_submit": signed_submit,
                "signed_submit_source": signed_submit_source,
                "submitted_signature": str(sig),
                "manual_request_ref": {
                    "client_order_id": manual_request.get("client_order_id"),
                    "request_fingerprint": manual_request.get("request_fingerprint"),
                },
                "session_caps": {
                    **session_caps,
                    "requested_notional_usd": requested_notional_usd,
                },
            }
            if pause_reset is not None:
                dispatch_payload["pause_reset"] = pause_reset
            self._live_send_session_orders_submitted += 1
            if requested_notional_usd is not None:
                self._live_send_session_notional_usd_submitted += float(requested_notional_usd)
            dispatch_payload["session_caps"]["orders_submitted_after"] = int(self._live_send_session_orders_submitted)
            dispatch_payload["session_caps"]["notional_usd_submitted_after"] = round(float(self._live_send_session_notional_usd_submitted), 6)
            self._live_send_runtime_counters["submit_dispatch_submitted"] += 1
            if bool(self.config.get("live_send_fetch_chain_reconciliation", False)):
                chain_fetch = {
                    "enabled": True,
                    "signature": str(sig),
                    "status_fetched": False,
                    "tx_fetched": False,
                    "error": "",
                }
                status_payload = None
                tx_payload = None
                try:
                    if hasattr(self.rpc_client, "get_signature_status"):
                        fetched_status = self.rpc_client.get_signature_status(str(sig))
                        status_payload = {"value": [fetched_status]}
                        chain_fetch["status_fetched"] = True
                    if hasattr(self.rpc_client, "get_transaction"):
                        fetched_tx = self.rpc_client.get_transaction(str(sig))
                        tx_payload = {"result": fetched_tx} if fetched_tx is not None else None
                        chain_fetch["tx_fetched"] = fetched_tx is not None
                    dispatch_payload["chain_reconciliation"] = reconcile_live_chain_confirmation(
                        workflow={
                            "final_decision": "confirmed",
                            "submit_confirm_summary": {
                                "outcome_class": "submit_confirm_confirmed",
                            },
                        },
                        signature_status_payload=status_payload,
                        tx_payload=tx_payload,
                        preview_estimates=workflow.get("estimated_costs"),
                        mismatch_thresholds=self.config.get("live_send_chain_reconciliation_thresholds"),
                        owner_filter=self.config.get("live_send_chain_owner_filter"),
                    )
                    chain_outcome = str((dispatch_payload.get("chain_reconciliation") or {}).get("outcome_class") or "")
                    if (
                        (chain_outcome == "live_reconciliation_mismatch" and bool(self.config.get("live_send_pause_on_reconciliation_mismatch", True)))
                        or (chain_outcome == "live_confirmation_inconclusive" and bool(self.config.get("live_send_pause_on_reconciliation_inconclusive", True)))
                    ):
                        self._live_send_pause_latched = True
                        self._live_send_pause_reason = chain_outcome
                        if chain_outcome == "live_reconciliation_mismatch":
                            self._live_send_runtime_counters["submit_dispatch_reconciliation_mismatch_latches"] += 1
                        if chain_outcome == "live_confirmation_inconclusive":
                            self._live_send_runtime_counters["submit_dispatch_reconciliation_inconclusive_latches"] += 1
                        dispatch_payload["pause_latch"] = {
                            "latched": True,
                            "reason": chain_outcome,
                        }
                except Exception as exc:
                    chain_fetch["error"] = str(exc)
                dispatch_payload["chain_reconciliation_fetch"] = chain_fetch
            dispatch_payload["runtime_counters"] = self._live_send_runtime_snapshot()
            return dispatch_payload

        out = {
            "mode": "submit_dispatch_stub_v1",
            "action": str(action),
            "attempted": False,
            "live_send_enabled": True,
            "live_send_network_enabled": live_send_network_enabled,
            "ready": False,
            "reason": "dex_executor_submit_stub_not_supported",
        }
        out["runtime_counters"] = self._live_send_runtime_snapshot()
        return out

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
        workflow = self._attach_quote_cost_estimate("buy", workflow, fallback_notional_usd=usd_size)
        workflow = self._attach_submit_confirm_skeleton("buy", workflow)
        workflow = self._attach_manual_submit_scaffold("buy", workflow)
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
        workflow = self._attach_quote_cost_estimate("sell", workflow, fallback_notional_usd=None)
        workflow = self._attach_submit_confirm_skeleton("sell", workflow)
        workflow = self._attach_manual_submit_scaffold("sell", workflow)
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
        workflow = self._attach_quote_cost_estimate("stop_loss", workflow, fallback_notional_usd=None)
        workflow = self._attach_submit_confirm_skeleton("stop_loss", workflow)
        workflow = self._attach_manual_submit_scaffold("stop_loss", workflow)
        return self._disabled_result(
            "stop_loss",
            "live adapter skeleton only (stop loss not implemented)" if not self._submit_skeleton_enabled() else "live adapter submit skeleton only (stop loss not implemented)",
            metadata={**metadata, "startup_guardrails": dict(self.startup_guardrails), **workflow},
            position_id=position_id,
        )

