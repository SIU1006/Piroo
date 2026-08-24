import io
from unittest.mock import patch  # MagicMock for async

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_returns_taskid():
    file = io.BytesIO(b"fake video content")
    with patch(
        "app.routes.upload.process_video.delay"
    ) as mock_delay:  # Mock Celery task so no need Redis
        response = client.post(
            "/api/v1/upload", files={"file": ("test.mp4", file, "video/mp4")}
        )
    assert response.status_code == 200
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "queued"
    mock_delay.assert_called_once()  # Ensure the Celery task was called


def test_no_file():
    response = client.post("/api/v1/upload")
    assert response.status_code == 422

def test_file_size(isolated_upload_dir, monkeypatch):
    monkeypatch.setattr("app.routes.upload.MAX_SIZE", 10) # Set max size as 10bytes for this test
    file = io.BytesIO(b"This line is already way more than 10 bytes")

    response = client.post(
        "/api/v1/upload", files={"file": ("test.mp4", file, "video/mp4")}
    )

    assert response.status_code == 413
    assert "1GB" in response.json()["detail"]

    assert list(isolated_upload_dir.iterdir()) == [] # Check if file is cleaned up