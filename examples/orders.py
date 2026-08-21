"""
A small order-processing service with a fake database.

Two code paths, so you can watch pyxtrace catch a regression:

    pyxtrace examples/orders.py -o before.pyxt          # batched lookup
    PYXTRACE_NPLUSONE=1 pyxtrace examples/orders.py -o after.pyxt
    pyxtrace diff before.pyxt after.pyxt                # exits 1

The second path fetches each order's customer individually — the N+1 query
pattern that is easy to introduce in review and hard to spot by reading.
"""

import os

N_ORDERS = 120


class Cursor:
    """Stand-in for a DB cursor. Counting calls is the point, not real I/O."""

    def __init__(self) -> None:
        self.queries = 0

    def execute(self, sql: str, *params: object) -> list:
        self.queries += 1
        # a little work so the row exists and the call is not optimised away
        return [{"id": p, "sql": sql} for p in (params or (0,))]


def fetch_orders(cur: Cursor, limit: int) -> list[dict]:
    rows = cur.execute("SELECT id, customer_id FROM orders LIMIT %s", limit)
    return [{"id": i, "customer_id": i % 20} for i in range(limit)]


def fetch_customer(cur: Cursor, customer_id: int) -> dict:
    cur.execute("SELECT * FROM customers WHERE id = %s", customer_id)
    return {"id": customer_id, "name": f"customer-{customer_id}"}


def fetch_customers(cur: Cursor, customer_ids: list[int]) -> dict:
    cur.execute("SELECT * FROM customers WHERE id = ANY(%s)", customer_ids)
    return {cid: {"id": cid, "name": f"customer-{cid}"} for cid in customer_ids}


def serialize(order: dict, customer: dict) -> dict:
    return {
        "order_id": order["id"],
        "customer": customer["name"],
        "total": order["id"] * 1.5,
    }


def process_order(cur: Cursor, order: dict, customers: dict | None) -> dict:
    if customers is None:
        # regression: one query per order
        customer = fetch_customer(cur, order["customer_id"])
    else:
        customer = customers[order["customer_id"]]
    return serialize(order, customer)


def handle_request(cur: Cursor, batched: bool) -> list[dict]:
    orders = fetch_orders(cur, N_ORDERS)
    customers = None
    if batched:
        customers = fetch_customers(cur, sorted({o["customer_id"] for o in orders}))
    return [process_order(cur, o, customers) for o in orders]


def main() -> None:
    batched = os.environ.get("PYXTRACE_NPLUSONE") != "1"
    cur = Cursor()
    results = handle_request(cur, batched)
    print(
        f"processed {len(results)} orders in {cur.queries} queries "
        f"({'batched' if batched else 'N+1'} path)"
    )


if __name__ == "__main__":
    main()
