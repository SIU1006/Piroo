import io
from unittest.mock import patch  # MagicMock for async

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.routes.upload import validate_extension

client = TestClient(app)

def test_accepts_allowed_type():
    assert validate_extension("clip.mp4") == ".mp4"


def test_case_insensitive():
    assert validate_extension("CLIP.MP4") == ".mp4"


def test_rejects_disallowed_type():
    with pytest.raises(HTTPException) as exc_info:
        validate_extension("document.pdf")
    assert exc_info.value.status_code == 415


def test_rejects_missing_filename():
    with pytest.raises(HTTPException) as exc_info:
        validate_extension(None)
    assert exc_info.value.status_code == 400


def test_rejects_empty_filename():
    with pytest.raises(HTTPException) as exc_info:
        validate_extension("")
    assert exc_info.value.status_code == 400

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