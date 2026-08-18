import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()  # Load .env into os env
assert os.getenv("BROKER_URL") is not None, "BROKER_URL is not set in .env"


celery_app = Celery(
    "worker",
    broker=os.getenv("BROKER_URL"),
)

celery_app.conf.update(
    task_soft_time_limit=1800,  # Raise Exception
    task_time_limit=1900,  # force-kill
    task_acks_late=True,  # Redis will re-queue the force-killed tasks
    worker_prefetch_multiplier=1,  # 1 worker 1 task
    
    # prevent worker crashing if redis is not ready yet
    broker_connection_retry_on_startup=True,  
)