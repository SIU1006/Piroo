import redis
from fastapi import APIRouter, WebSocket
import asyncio
import os

router = APIRouter()

@router.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    r = redis.Redis.from_url(os.getenv("BROKER_URL", "redis://localhost:6379/0"))  

    # Check for stored result in Redis before subscribe
    stored = r.get(f"result:{task_id}")
    if stored:
        await websocket.send_text(stored.decode("utf-8"))  
        await websocket.close()
        return

    # Otherwise normal sub
    pubsub = r.pubsub()
    pubsub.subscribe(f"task:{task_id}")

    loop = asyncio.get_event_loop() # Separate thread
    data = await loop.run_in_executor(None, wait_for_message, pubsub)
    await websocket.send_text(data.decode('utf-8'))
    await websocket.close()

def wait_for_message(pubsub): # regular function that does the blocking listen loop for redis pubsub.listen()
    for message in pubsub.listen():
        if message['type'] == 'message':
            return message['data']