import asyncio
import os

from fastapi import APIRouter, WebSocket
from redis import asyncio as redis

router = APIRouter()

BROKER_URL = os.getenv("BROKER_URL", "redis://localhost:6379/0")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

RESULT_WAIT_TIMEOUT_SEC = int(os.getenv("RESULT_WAIT_TIMEOUT_SEC", "300"))
KEEPALIVE_INTERVAL_SEC = int(os.getenv("KEEPALIVE_INTERVAL_SEC", "30"))

def _redis_url() -> str:
    if not REDIS_PASSWORD or "@" in BROKER_URL:
        return BROKER_URL
    scheme, _, rest = BROKER_URL.partition("://")
    return f"{scheme}://:{REDIS_PASSWORD}@{rest}"


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
            data = await wait_with_keepalive(websocket, pubsub, RESULT_WAIT_TIMEOUT_SEC)
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

async def wait_with_keepalive(websocket: WebSocket, pubsub, overall_timeout_sec: float):
    '''
    Wait for a pubsub message up to overall_timeout_sec seconds, but pings the client every KEEPALIVE_INTERVAL_SEC while waiting to keep it alive.
    A long asyncio.wait_for() sends nothing to the client for up to 5mins, many browsers/proxies/load balances will treat it as a dead connection and close before our own timeout ever fires.
    '''
    loop = asyncio.get_event_loop()
    deadline = loop.time() + overall_timeout_sec

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError

        try:
            return await asyncio.wait_for(
                wait_for_message(pubsub), timeout=min(KEEPALIVE_INTERVAL_SEC, remaining)
            )
        except TimeoutError:
            # Still no message from pubsub but KEEPALIVE_INTERVAL_SEC has passed
            # >> ping the client
            await websocket.send_json({"status": "processing"})

async def wait_for_message(pubsub):
    while True:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message is not None and message["type"] == "message":
            return message["data"]