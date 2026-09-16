import asyncio
import logging
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, File, HTTPException, UploadFile

import task_store
import upload_storage
from app.schemas.task import UploadResponse
from settings import UPLOAD_DIR
from worker.tasks import process_video

router = APIRouter()
logger = logging.getLogger(__name__)
UPLOAD_DIR.mkdir(exist_ok=True)  # exist_ok = ok to already have the folder, dont crash

# File Validation
MAX_SIZE = 1024 * 1024 * 1024  # 1 GB
ALLOWED_EXTENSIONS = {".mp4", ".mp3", ".mov", ".wav", ".m4a"}
CHUNK_SIZE = 1024 * 1024 #1mb

# =================== upload_file() helpers ========================
def validate_extension(filename: str | None) -> str:
    # Checkings - filename, extension
    if not filename:
        raise HTTPException(status_code=400, detail="No file uploaded")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415, detail=f"File type {extension} not allowed"
        )
    return extension

async def save_upload(file: UploadFile, file_path: Path) -> None:
    '''Send upload to disk in chunks.
    Removes partial file and re-raises on any failure,
    so callers never need to handle their own cleanup.
    '''
    file_size = 0
    try:
        async with aiofiles.open(file_path, "wb") as buffer:
            while chunk := await file.read(CHUNK_SIZE):
                file_size += len(chunk)
                if file_size > MAX_SIZE:  # Check size
                    raise HTTPException(
                        status_code=413, detail="File size exceeds 1GB limit"
                    )
                await buffer.write(chunk)
    except Exception:
        file_path.unlink(missing_ok=True)
        raise
# ===================================================================

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    extension = validate_extension(file.filename)
    task_id = str(uuid.uuid4())
    file_path = UPLOAD_DIR / f"{task_id}{extension}"  # keep extension

    r = task_store.client()
    ref = upload_storage.reference(task_id, extension, file_path)
    reserved = False
    enqueued = False
    try:
        reserved = await asyncio.to_thread(task_store.reserve, r, task_id, ref)
        if not reserved:
            raise HTTPException(status_code=429, detail="Task capacity is full",
                                headers={"Retry-After": "30"})
        try:
            await asyncio.wait_for(save_upload(file, file_path), task_store.UPLOAD_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise HTTPException(status_code=408, detail="Upload timed out") from exc
        await asyncio.to_thread(upload_storage.persist, file_path, ref)
        ready = await asyncio.to_thread(task_store.transition, r, task_id, "queued", "waiting")
        if not ready:
            raise HTTPException(status_code=408, detail="Upload reservation expired")
        # The broker call is synchronous. Keep it off the API event loop.
        await asyncio.to_thread(process_video.apply_async, args=(task_id, ref), task_id=task_id)
        enqueued = True
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not enqueue upload task %s", task_id)
        raise HTTPException(status_code=503, detail="Task queue is unavailable") from exc
    finally:
        if not enqueued:
            file_path.unlink(missing_ok=True)
            if reserved:
                try:
                    await asyncio.to_thread(upload_storage.delete, ref)
                    await asyncio.to_thread(task_store.cancel, r, task_id)
                except Exception:
                    logger.exception("Could not release failed upload %s; sweeper will retry", task_id)
        elif ref.startswith("s3://"):
            file_path.unlink(missing_ok=True)
        await asyncio.to_thread(r.close)
        await file.close()
    return UploadResponse(filename=file.filename, task_id=task_id, status="queued")
