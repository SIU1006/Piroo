from celery import Celery
from dotenv import load_dotenv
import os

load_dotenv() # Load .env into os env
assert os.getenv("BROKER_URL") is not None, "BROKER_URL is not set in .env"
assert os.getenv("BACKEND_URL") is not None, "BACKEND_URL is not set in .env"


celery_app = Celery(
    "worker",
    broker=os.getenv("BROKER_URL"),
    backend=os.getenv("BACKEND_URL")
)