import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas.task import UploadResponse
from settings import UPLOAD_DIR
from worker.tasks import process_video

router = APIRouter()
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

    await save_upload(file,file_path)

    process_video.delay(task_id, str(file_path))  # Pass next step to celery worker
    return UploadResponse(filename=file.filename, task_id=task_id, status="queued")
