import os

import pytest
from store.app import Receipt, Store, seed_database

DATABASE_URL = os.getenv("BROKEN_STORE_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="BROKEN_STORE_TEST_DATABASE_URL is not set")
def test_checkout_against_postgresql() -> None:
    assert DATABASE_URL
    seed_database(DATABASE_URL)

    receipt, query_count = Store(DATABASE_URL).checkout()

    assert receipt == Receipt(item_count=50, total_cents=127_500)
    assert query_count == int(os.getenv("EXPECTED_QUERY_COUNT", "2"))
