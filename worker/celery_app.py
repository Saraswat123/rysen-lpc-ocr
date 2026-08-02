"""Celery app — Redis as broker + backend."""
import os
import sys
import pathlib

# Ensure project root on sys.path so forked workers can import extractor/api/models
_ROOT = str(pathlib.Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery = Celery(
    "lpc_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["worker.tasks"],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    task_track_started=True,
    worker_prefetch_multiplier=1,   # one task at a time per worker (PDF is heavy)
    task_acks_late=True,            # re-queue on worker crash
)
