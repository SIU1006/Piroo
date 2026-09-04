import logging
import os

from prometheus_client import Counter, Gauge, Histogram, start_http_server

'''
Prometheus metrics for Celery tasks.
'''

logger = logging.getLogger(__name__)

METRICS_PORT = int(os.getenv("CELERY_METRICS_PORT", "9808"))

TASK_TOTAL = Counter(
    "celery_task_total",
    "Total Celery tasks processed, by task name and outcome.",
    labelnames=["task_name", "status"],
)

TASK_DURATION_SECONDS = Histogram(
    "celery_task_duration_seconds",
    "Task execution wall-clock time, by task name and input video length bucket.",
    labelnames=["task_name", "video_length_bucket"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 900, 1800, float("inf")),
)

TASK_FAILURES_TOTAL = Counter(
    "celery_task_failures_total",
    "Task failures, by task name and exception type.",
    labelnames=["task_name", "exception_type"],
)

# Rolling WER from canary checks
CANARY_WER = Gauge(
    "canary_wer",
    "Word error rate of the most recent synthetic canary check against known reference clips.",
)

_started = False


def ensure_metrics_server_started() -> None:
    global _started
    if _started:
        return
    start_http_server(METRICS_PORT)
    logger.info(f"Celery metrics server listening on :{METRICS_PORT}/metrics")
    _started = True