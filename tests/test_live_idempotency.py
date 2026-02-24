from src.live.idempotency import (
    FileBackedIdempotencyStore,
    InMemoryIdempotencyStore,
    build_client_order_id,
    build_request_fingerprint,
)


def test_build_client_order_id_is_deterministic_for_same_inputs():
    a = build_client_order_id("buy", "TOKEN", "SYM", 0.01, 100, sequence=1)
    b = build_client_order_id("buy", "TOKEN", "SYM", 0.01, 100, sequence=1)
    c = build_client_order_id("buy", "TOKEN", "SYM", 0.01, 100, sequence=2)
    assert a == b
    assert a != c
    assert a.startswith("coid_buy_")


def test_build_request_fingerprint_changes_with_input():
    a = build_request_fingerprint("buy", "TOKEN", "SYM", 0.01, 100)
    b = build_request_fingerprint("buy", "TOKEN", "SYM", 0.01, 101)
    assert a != b


def test_in_memory_idempotency_store_suppresses_duplicates():
    store = InMemoryIdempotencyStore()
    d1 = store.decide_once("abc")
    d2 = store.decide_once("abc")
    d3 = store.decide_once("xyz")
    assert d1.allowed is True
    assert d2.allowed is False
    assert d2.reason == "duplicate_request"
    assert d3.allowed is True


def test_file_backed_idempotency_store_persists_duplicates_across_instances(tmp_path):
    path = tmp_path / "idempotency_keys.txt"
    store1 = FileBackedIdempotencyStore(str(path))
    d1 = store1.decide_once("abc")
    d2 = store1.decide_once("abc")

    store2 = FileBackedIdempotencyStore(str(path))
    d3 = store2.decide_once("abc")
    d4 = store2.decide_once("xyz")

    assert d1.allowed is True
    assert d2.allowed is False
    assert d3.allowed is False
    assert d4.allowed is True


def test_file_backed_idempotency_store_reset_clears_disk_state(tmp_path):
    path = tmp_path / "idempotency_keys.txt"
    store = FileBackedIdempotencyStore(str(path))
    assert store.decide_once("abc").allowed is True
    store.reset()
    store2 = FileBackedIdempotencyStore(str(path))
    assert store2.decide_once("abc").allowed is True
