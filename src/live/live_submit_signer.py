import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class SubmitSignerProtocol(Protocol):
    def build_signed_submit(self, order_preview: dict[str, Any], client_order_id: str, context: dict[str, Any] | None = None) -> dict[str, Any]: ...


class StaticSubmitSigner:
    """
    Supervised signer scaffold: returns a pre-provided base64 transaction payload.
    This is a bridge for pilot wiring; it does not perform cryptographic signing.
    """

    def __init__(self, *, transaction_base64: str | None = None, transaction_base64_path: str | None = None, label: str = "static_submit_signer_v1"):
        self.transaction_base64 = str(transaction_base64 or "").strip()
        self.transaction_base64_path = str(transaction_base64_path or "").strip()
        self.label = str(label or "static_submit_signer_v1")

    def _load_tx(self) -> tuple[str | None, str]:
        if self.transaction_base64:
            return self.transaction_base64, "config_string"
        if self.transaction_base64_path:
            p = Path(self.transaction_base64_path)
            if not p.exists():
                return None, "file_not_found"
            text = p.read_text(encoding="utf-8").strip()
            if not text:
                return None, "file_empty"
            return text, str(p)
        return None, "missing_config"

    def build_signed_submit(self, order_preview: dict[str, Any], client_order_id: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        tx_b64, source = self._load_tx()
        return {
            "mode": "signed_submit_from_signer",
            "label": self.label,
            "client_order_id": str(client_order_id),
            "order_action": (order_preview or {}).get("action"),
            "transaction_base64": tx_b64,
            "ready": bool(tx_b64),
            "source": source,
            "reason": "" if tx_b64 else "submit_signer_missing_transaction_base64",
            "context": dict(context or {}),
        }


def _default_command_runner(command: list[str], payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=float(timeout_seconds),
        check=False,
    )
    if proc.returncode != 0:
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        details = []
        if stdout:
            details.append(f"stdout={stdout}")
        if stderr:
            details.append(f"stderr={stderr}")
        suffix = f" ({'; '.join(details)})" if details else ""
        raise RuntimeError(f"signer command failed with exit code {proc.returncode}{suffix}")
    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise RuntimeError("signer command returned empty stdout")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("signer command stdout is not valid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("signer command output must be a JSON object")
    return data


class CommandSubmitSigner:
    """
    External signer bridge using JSON stdin/stdout. Keeps key material outside the bot process.
    The external command is responsible for building/signing and returning a base64 transaction.
    """

    def __init__(
        self,
        *,
        command: list[str] | tuple[str, ...],
        timeout_seconds: float = 10.0,
        runner: Callable[[list[str], dict[str, Any], float], dict[str, Any]] | None = None,
        label: str = "command_submit_signer_v1",
    ):
        cmd = [str(part) for part in list(command or []) if str(part)]
        if not cmd:
            raise ValueError("command must be a non-empty list")
        self.command = cmd
        self.timeout_seconds = float(timeout_seconds)
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self.runner = runner or _default_command_runner
        self.label = str(label or "command_submit_signer_v1")

    def build_signed_submit(self, order_preview: dict[str, Any], client_order_id: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        request = {
            "client_order_id": str(client_order_id),
            "order_preview": dict(order_preview or {}),
            "context": dict(context or {}),
        }
        response = self.runner(self.command, request, self.timeout_seconds)
        tx_b64 = response.get("transaction_base64")
        if tx_b64 in (None, ""):
            tx_b64 = response.get("transactionBase64")
        tx_b64 = None if tx_b64 in (None, "") else str(tx_b64)
        reason = "" if tx_b64 else str(response.get("reason") or "submit_signer_missing_transaction_base64")
        return {
            "mode": "signed_submit_from_signer",
            "label": self.label,
            "client_order_id": str(client_order_id),
            "order_action": (order_preview or {}).get("action"),
            "transaction_base64": tx_b64,
            "ready": bool(tx_b64),
            "source": "command_signer",
            "reason": reason,
            "context": dict(context or {}),
            "signer_response": dict(response),
            "command": list(self.command),
        }
