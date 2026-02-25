from pathlib import Path
from typing import Any


class SignedSubmitStubExecutor:
    """
    Wraps an existing dex executor and provides a file/static backed signed submit stub.
    This is a supervised-pilot bridge: it does not build/sign transactions itself.
    """

    def __init__(
        self,
        base_executor: Any,
        *,
        transaction_base64: str | None = None,
        transaction_base64_path: str | None = None,
        label: str = "signed_submit_stub_file_v1",
    ):
        self._base = base_executor
        self._tx_b64 = str(transaction_base64 or "").strip()
        self._tx_b64_path = str(transaction_base64_path or "").strip()
        self._label = str(label or "signed_submit_stub_file_v1")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    def _load_transaction_base64(self) -> tuple[str | None, str]:
        if self._tx_b64:
            return self._tx_b64, "config_string"
        if self._tx_b64_path:
            path = Path(self._tx_b64_path)
            if not path.exists():
                return None, "file_not_found"
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                return None, "file_empty"
            return text, str(path)
        return None, "missing_config"

    def build_signed_submit_stub(self, order_preview: dict[str, Any], client_order_id: str) -> dict[str, Any]:
        tx_b64, source = self._load_transaction_base64()
        return {
            "mode": "signed_submit_stub",
            "label": self._label,
            "client_order_id": str(client_order_id),
            "order_action": (order_preview or {}).get("action"),
            "transaction_base64": tx_b64,
            "ready": bool(tx_b64),
            "source": source,
            "reason": "" if tx_b64 else "signed_submit_stub_missing_transaction_base64",
        }

