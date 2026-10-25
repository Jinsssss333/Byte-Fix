from demo_app.tasks import send_receipt


def _order(email="Asha@Example.com"):
    return {
        "id": 1,
        "customer": {"name": "Asha", "email": email},
        "items": [{"price": 10.0, "qty": 2}, {"price": 5.5, "qty": 1}],
    }


def test_receipt_total():
    assert "$25.50" in send_receipt.run(_order())


def test_email_is_lowercased():
    assert "asha@example.com" in send_receipt.run(_order())


def test_receipt_mentions_order_id():
    assert "order 1" in send_receipt.run(_order())
