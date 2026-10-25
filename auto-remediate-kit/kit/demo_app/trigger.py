"""Trigger demo failures.

  python -m demo_app.trigger --case null      # primary bug
  python -m demo_app.trigger --case decimal   # stretch bug
  python -m demo_app.trigger --case external  # cannot-reproduce case

Default mode is eager (runs in this process, no worker or Redis needed).
Use --mode queue to send the task to a running Celery worker instead.
"""
import argparse

from . import tasks

ORDER_NULL_EMAIL = {
    "id": 42,
    "customer": {"name": "Asha Rao", "email": None},
    "items": [{"price": 10.0, "qty": 2}, {"price": 4.5, "qty": 1}],
}
ORDER_OK = {
    "id": 43,
    "customer": {"name": "Ravi K", "email": "Ravi@Example.com"},
    "items": [{"price": 7.0, "qty": 3}],
}

CASES = {
    "null": (tasks.send_receipt, (ORDER_NULL_EMAIL,)),
    "decimal": (tasks.generate_invoice, (ORDER_OK,)),
    "external": (tasks.convert_total, (120.0, "EUR")),
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--case", choices=CASES, default="null")
    p.add_argument("--mode", choices=["eager", "queue"], default="eager")
    a = p.parse_args()
    task, args = CASES[a.case]
    if a.mode == "queue":
        r = task.delay(*args)
        print(f"queued {task.name} id={r.id}")
    else:
        r = task.apply(args=args)
        print(f"ran {task.name} eagerly -> state={r.state}")


if __name__ == "__main__":
    main()
