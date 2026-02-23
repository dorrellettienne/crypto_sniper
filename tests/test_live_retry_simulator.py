from src.live.retry_simulator import retry_operation


def test_retry_operation_succeeds_after_retry():
    state = {"n": 0}

    def op():
        state["n"] += 1
        if state["n"] < 2:
            raise RuntimeError("temporary")
        return "ok"

    result = retry_operation(op, max_attempts=3)

    assert result["ok"] is True
    assert result["attempts"] == 2
    assert result["result"] == "ok"
    assert result["errors"] == ["temporary"]


def test_retry_operation_stops_on_non_retryable():
    def op():
        raise ValueError("fatal")

    result = retry_operation(op, max_attempts=3, should_retry=lambda exc: not isinstance(exc, ValueError))

    assert result["ok"] is False
    assert result["attempts"] == 1
    assert result["errors"] == ["fatal"]
