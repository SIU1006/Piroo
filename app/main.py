from fastapi import FastAPI
from app.routes.upload import router
from app.routes.websocket import router as websocket_router #!!!
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from prometheus_fastapi_instrumentator import Instrumentator
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

app = FastAPI(
    title="Async Video to Text API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(router, prefix="/api/v1") # uploading
app.include_router(websocket_router, prefix="/api/v1") # websocket

# Serve html from FASTAPI
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

Instrumentator().instrument(app).expose(app) # expose /metrics endpoint for Prometheus
# must be after all routes are registered, otherwise it will not instrument them