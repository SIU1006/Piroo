from fastapi import APIRouter, UploadFile, File, HTTPException
import shutil
from pathlib import Path
import uuid
from app.schemas.task import UploadResponse
from worker.tasks import process_video

router = APIRouter()
UPLOAD_DIR = Path("uploads") # Create path object for uploads folder
UPLOAD_DIR.mkdir(exist_ok=True) # exist_ok = ok to already have the folder, dont crash

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")
    
    task_id = str(uuid.uuid4())
    file_path = UPLOAD_DIR / task_id # / join path
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer) # Chunk the video to avoid memory issues for large files

    process_video.delay(task_id, str(file_path)) # Pass next step to celery worker
    return UploadResponse(filename=file.filename, task_id=task_id, status="queued")