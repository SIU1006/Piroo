from celery import Celery
from dotenv import load_dotenv
import os

load_dotenv() # Load .env into os env


celery_app = Celery(
    "worker",
    broker=os.getenv("BROKER_URL"),
    backend=os.getenv("BACKEND_URL")
)