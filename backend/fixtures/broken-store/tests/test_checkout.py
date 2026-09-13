import os

import psycopg
import store.app as store_app
from fastapi.testclient import TestClient


class FakeCursor:
    def __init__(self) -> None:
        self.rows: list[tuple[int, ...]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, parameters: tuple = ()) -> None:
        if "cart_items" in query:
            self.rows = [(product_id, 1) for product_id in range(1, 51)]
        elif "ANY" in query:
            self.rows = [(product_id, product_id * 100) for product_id in parameters[0]]
        else:
            product_id = parameters[0]
            self.rows = [(product_id * 100,)]

    def fetchall(self) -> list[tuple[int, ...]]:
        return self.rows

    def fetchone(self) -> tuple[int, ...]:
        return self.rows[0]


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return FakeCursor()


def test_checkout_receipt_stays_correct(monkeypatch) -> None:
    monkeypatch.setattr(psycopg, "connect", lambda _: FakeConnection())

    receipt, query_count = store_app.Store("postgresql://unused").checkout()

    assert receipt == store_app.Receipt(item_count=50, total_cents=127_500)
    expected_query_count = os.getenv("EXPECTED_QUERY_COUNT")
    if expected_query_count:
        assert query_count == int(expected_query_count)


def test_checkout_endpoint_returns_receipt(monkeypatch) -> None:
    class FakeStore:
        def checkout(self) -> tuple[store_app.Receipt, int]:
            return store_app.Receipt(item_count=50, total_cents=127_500), 2

    monkeypatch.setattr(store_app, "store", FakeStore())
    response = TestClient(store_app.app).post("/checkout")

    assert response.status_code == 200
    assert response.json() == {"item_count": 50, "total_cents": 127_500}
    assert response.headers["X-Database-Query-Count"] == "2"
