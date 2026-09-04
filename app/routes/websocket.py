import asyncio
import os

from fastapi import APIRouter, WebSocket
from redis import asyncio as redis

router = APIRouter()

BROKER_URL = os.getenv("BROKER_URL", "redis://localhost:6379/0")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

RESULT_WAIT_TIMEOUT_SEC = 1900 + 30


def _redis_url() -> str:
    if not REDIS_PASSWORD or "@" in BROKER_URL:
        return BROKER_URL
    scheme, _, rest = BROKER_URL.partition("://")
    return f"{scheme}://default:{REDIS_PASSWORD}@{rest}"


# One shared client & connection pool reused by every websocket connection
def get_client():
    return redis.Redis.from_url(_redis_url())


@router.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()

    r_client = get_client()
    pubsub = r_client.pubsub()
    try:
        # Need to sub first before checking cache to avoid race condition
        await pubsub.subscribe(f"task:{task_id}")

        # Check cache in Redis
        stored = await r_client.get(f"result:{task_id}")
        if stored:
            await websocket.send_text(stored.decode("utf-8"))
            return

        try:
            data = await asyncio.wait_for(
                wait_for_message(pubsub), timeout=RESULT_WAIT_TIMEOUT_SEC
            )
        except TimeoutError:
            await websocket.send_text(
                '{"status": "error", "message": "Timed out waiting for the task to finish."}'
            )
            return

        await websocket.send_text(data.decode("utf-8"))

    finally: # Always close redis sub and socket
        await pubsub.unsubscribe(f"task:{task_id}")
        await pubsub.close()
        await websocket.close()


async def wait_for_message(pubsub):
    while True:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message is not None and message["type"] == "message":
            return message["data"]
