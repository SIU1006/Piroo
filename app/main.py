import asyncio
import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

import task_store
import upload_storage
from app.routes.tasks import router as tasks_router
from app.routes.upload import router
from app.routes.websocket import router as websocket_router

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

app = FastAPI(title="Async Video to Text API", version="1.0.0")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

app.include_router(router, prefix="/api/v1")  # uploading
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(websocket_router, prefix="/api/v1")  # websocket

# Serve html from FASTAPI
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.get("/readyz")
async def ready():
    def check_dependencies():
        with task_store.client() as r:
            r.ping()
        if os.getenv("UPLOAD_STORAGE_BACKEND", "local") == "s3":
            upload_storage.client(read_timeout=3).list_objects_v2(
                Bucket=os.environ["S3_UPLOAD_BUCKET"], Prefix="uploads/", MaxKeys=1,
            )
    try:
        await asyncio.to_thread(check_dependencies)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Queue or upload storage is unavailable") from exc
    return {"status": "ready"}


Instrumentator().instrument(app).expose(app)  # expose /metrics endpoint for Prometheus
# must be after all routes are registered, otherwise it will not instrument them
