from fastapi.testclient import TestClient
from app.main import app
from unittest.mock import patch
import io

client = TestClient(app)

def test_upload_returns_taskid():
    fake_file = io.BytesIO(b"fake video content")
    with patch("app.routes.upload.process_video.delay") as mock_delay: # Mock Celery task so no need Redis
            response = client.post(
                "/api/v1/upload",
                files={"file": ("test.mp4", fake_file, "video/mp4")}
            )
    assert response.status_code == 200
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "queued"
    mock_delay.assert_called_once()  # Ensure the Celery task was called

def test_upload_no_file_returns_400():
    response = client.post("/api/v1/upload")
    assert response.status_code == 422