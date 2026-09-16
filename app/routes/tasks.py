import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException
from redis.exceptions import RedisError

import task_store

router = APIRouter()


@router.get("/tasks/{task_id}")
async def get_task(task_id: UUID):
    r = task_store.client()
    try:
        result = await asyncio.to_thread(task_store.get, r, str(task_id))
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Task status is unavailable") from exc
    finally:
        await asyncio.to_thread(r.close)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found or expired")
    return result
