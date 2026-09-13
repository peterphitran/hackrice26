from __future__ import annotations

import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Response
from pydantic import BaseModel

CART_SIZE = 50
DATABASE_URL = os.getenv(
    "BROKEN_STORE_DATABASE_URL",
    "postgresql://lou:lou@localhost:5432/lou",
)


class Receipt(BaseModel):
    item_count: int
    total_cents: int


def seed_database(database_url: str) -> None:
    """Replace the fixture schema with the same dataset before every application run."""
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS broken_store CASCADE")
        cursor.execute("CREATE SCHEMA broken_store")
        cursor.execute(
            "CREATE TABLE broken_store.products "
            "(id INTEGER PRIMARY KEY, price_cents INTEGER NOT NULL)"
        )
        cursor.execute(
            "CREATE TABLE broken_store.cart_items "
            "(product_id INTEGER PRIMARY KEY, quantity INTEGER NOT NULL)"
        )
        cursor.executemany(
            "INSERT INTO broken_store.products VALUES (%s, %s)",
            ((product_id, product_id * 100) for product_id in range(1, CART_SIZE + 1)),
        )
        cursor.executemany(
            "INSERT INTO broken_store.cart_items VALUES (%s, 1)",
            ((product_id,) for product_id in range(1, CART_SIZE + 1)),
        )


class Store:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def checkout(self) -> tuple[Receipt, int]:
        query_count = 0
        with psycopg.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT product_id, quantity FROM broken_store.cart_items")
            query_count += 1
            cart = cursor.fetchall()
            product_ids = [product_id for product_id, _ in cart]
            cursor.execute(
                "SELECT id, price_cents FROM broken_store.products WHERE id = ANY(%s)",
                (product_ids,),
            )
            query_count += 1
            products = dict(cursor.fetchall())

        receipt = Receipt(
            item_count=sum(quantity for _, quantity in cart),
            total_cents=sum(products[product_id] * quantity for product_id, quantity in cart),
        )
        return receipt, query_count


store = Store(DATABASE_URL)


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed_database(DATABASE_URL)
    yield


app = FastAPI(title="Broken Store", lifespan=lifespan)


@app.post("/checkout", response_model=Receipt)
def checkout(response: Response) -> Receipt:
    receipt, query_count = store.checkout()
    response.headers["X-Database-Query-Count"] = str(query_count)
    return receipt
