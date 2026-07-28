from fastapi import FastAPI
from app.routes.upload import router
from app.routes.websocket import router as websocket_router #!!!

app = FastAPI(
    title="Asnyc Video to Text API",
    version="1.0.0"
)

app.include_router(router, prefix="/api/v1") # For uploading
app.include_router(websocket_router, prefix="/api/v1") # For websocket