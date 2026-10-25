"""Demo Celery tasks for an order-processing service."""
import json
from decimal import Decimal

from .celery_app import app


@app.task(name="demo_app.tasks.send_receipt")
def send_receipt(order: dict) -> str:
    """Build a receipt string for an order."""
    email = order["customer"]["email"].lower()
    total = sum(item["price"] * item["qty"] for item in order["items"])
    return f"Receipt for {email}: ${total:.2f} (order {order['id']})"


@app.task(name="demo_app.tasks.generate_invoice")
def generate_invoice(order: dict) -> str:
    """Return the invoice for an order as a JSON string."""
    subtotal = sum(Decimal(str(i["price"])) * i["qty"] for i in order["items"])
    tax = subtotal * Decimal("0.18")
    invoice = {"order_id": order["id"], "subtotal": subtotal, "tax": tax}
    return json.dumps(invoice)


def _fetch_rate(currency: str) -> float:
    # Stands in for a call to a third-party exchange-rate API that is flaky.
    raise TimeoutError(f"rates API timed out after 5s for {currency}")


@app.task(name="demo_app.tasks.convert_total")
def convert_total(amount: float, currency: str) -> float:
    """Convert an amount into another currency."""
    rate = _fetch_rate(currency)
    return amount * rate
