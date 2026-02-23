import sys
from pathlib import Path
from typing import Any

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.jupiter import JupiterClient, JupiterQuoteError


class DummyResponse:
    def __init__(self, status_code: int, payload: Any = None, json_error: Exception | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class DummySession:
    def __init__(self, response: DummyResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any], timeout: float) -> DummyResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise RuntimeError("No dummy response configured")
        return self.response


def make_client(session: DummySession) -> JupiterClient:
    return JupiterClient(
        base_url="https://quote-api.jup.ag",
        quote_path="/v6/quote",
        timeout_s=10.0,
        session=session,
    )


def test_get_quote_success_returns_json() -> None:
    session = DummySession(
        response=DummyResponse(200, {"outAmount": "123", "routePlan": [{"swapInfo": {}}]})
    )
    client = make_client(session)

    result = client.get_quote("mintA", "mintB", 1000, 200)

    assert isinstance(result, dict)
    assert result["outAmount"] == "123"
    assert session.calls[0]["url"] == "https://quote-api.jup.ag/v6/quote"


def test_get_quote_non_200_raises() -> None:
    session = DummySession(response=DummyResponse(500, {"error": "oops"}))
    client = make_client(session)

    with pytest.raises(JupiterQuoteError, match="status 500"):
        client.get_quote("mintA", "mintB", 1000, 200)


def test_get_quote_invalid_json_raises() -> None:
    session = DummySession(response=DummyResponse(200, json_error=ValueError("bad json")))
    client = make_client(session)

    with pytest.raises(JupiterQuoteError, match="invalid JSON"):
        client.get_quote("mintA", "mintB", 1000, 200)


def test_get_quote_missing_fields_raises() -> None:
    session = DummySession(response=DummyResponse(200, {"foo": "bar"}))
    client = make_client(session)

    with pytest.raises(JupiterQuoteError, match="missing expected fields"):
        client.get_quote("mintA", "mintB", 1000, 200)


def test_route_exists_true_on_success() -> None:
    session = DummySession(response=DummyResponse(200, {"outAmount": "123"}))
    client = make_client(session)

    assert client.route_exists("mintA", "mintB", 1000, 200) is True


def test_route_exists_false_on_error() -> None:
    session = DummySession(error=requests.RequestException("network down"))
    client = make_client(session)

    assert client.route_exists("mintA", "mintB", 1000, 200) is False
