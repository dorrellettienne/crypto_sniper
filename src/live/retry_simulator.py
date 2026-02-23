from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def retry_operation(
    operation: Callable[[], T],
    max_attempts: int = 3,
    should_retry: Callable[[Exception], bool] | None = None,
) -> dict:
    """
    Retries an operation in-process (no sleeps) for dry-run/live-path simulation.
    Returns structured retry metadata.
    """
    if max_attempts <= 0:
        raise ValueError("max_attempts must be > 0")

    attempts = 0
    errors: list[str] = []
    while attempts < max_attempts:
        attempts += 1
        try:
            result = operation()
            return {"ok": True, "attempts": attempts, "result": result, "errors": errors}
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            if should_retry is not None and not should_retry(exc):
                break
            if attempts >= max_attempts:
                break

    return {"ok": False, "attempts": attempts, "result": None, "errors": errors}
