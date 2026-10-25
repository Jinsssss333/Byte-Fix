"""Celery app for the demo. Broker defaults to local Redis."""
import os

from celery import Celery

app = Celery(
    "demo_app",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    include=["demo_app.tasks"],
)
app.conf.task_serializer = "json"
app.conf.accept_content = ["json"]

# Auto-Remediate capture hook. Optional until autoremediate/hook.py exists.
try:
    from autoremediate.hook import install as _install_hook

    _install_hook()
except ImportError:
    pass
