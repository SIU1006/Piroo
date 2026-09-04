import os

from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

from settings import with_password

load_dotenv()  # Load .env into os env
_broker_url = os.getenv("BROKER_URL")
assert _broker_url is not None, "BROKER_URL is not set in .env"
BROKER_URL = with_password(_broker_url, os.getenv("REDIS_PASSWORD"))


celery_app = Celery(
    "worker",
    broker=BROKER_URL,
)

celery_app.conf.update(
    # Set both broker_read_url/broker_write_url explicitly so they cannot fall back to the uncredentialed BROKER_URL by k8s ConfigMap.
    broker_read_url=BROKER_URL,
    broker_write_url=BROKER_URL,
    task_soft_time_limit=1800,  # Raise Exception
    task_time_limit=1900,  # force-kill
    task_acks_late=True,  # Redis will re-queue the force-killed tasks
    worker_prefetch_multiplier=1,  # 1 worker 1 task

    # prevent worker crashing if redis is not ready yet
    broker_connection_retry_on_startup=True,

    broker_transport_options={
        "health_check_interval": 0,
        "retry_on_timeout": False,
    },
)

celery_app.conf.beat_schedule = {
    "sweep-stuck-tasks": {
        "task": "sweep_stuck_tasks",
        "schedule": 60.0,
    },
    "check-canary-wer": {
    "task": "check_canary_wer",
    "schedule": crontab(minute=0),
    }
}
