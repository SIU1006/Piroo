import json
from unittest.mock import MagicMock, patch

import pytest

from settings import UPLOAD_DIR
from worker.tasks import process_video

'''
Unit testing all external dependencies (ffmpeg, requests, ollama, redis).

This tests process_video's logic:
- duration, validation, error handling, what gets published.
'''

@pytest.fixture
def mock_pipeline(tmp_path, monkeypatch):
    with patch("worker.tasks.ffmpeg") as mock_ffmpeg, \
        patch("worker.tasks.requests") as mock_requests, \
        patch("worker.tasks.ollama") as mock_ollama, \
        patch("worker.tasks.redis") as mock_redis:

        mock_ffmpeg.probe.return_value = {"format": {"duration": 120.0}}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = "fake transcript"
        mock_requests.post.return_value = mock_response

        mock_ollama_client = MagicMock()
        mock_ollama_client.chat.return_value.message.content = "a short summary"

        mock_ollama.Client.return_value = mock_ollama_client # CAll API -> REturn this

        mock_redis_instance = MagicMock()
        mock_redis.Redis.from_url.return_value = mock_redis_instance

        yield {
            "ffmpeg": mock_ffmpeg,
            "requests": mock_requests,
            "ollama": mock_ollama_client,
            "redis": mock_redis_instance
        }

def write_fake_audio(task_id: str) -> None:
    audio_path = UPLOAD_DIR / f"{task_id}.mp3"
    audio_path.write_bytes(b"fake audio")

def test_success(mock_pipeline, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path) # Separate test result folder
    (tmp_path / "uploads").mkdir()

    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"fake bytes")

    write_fake_audio("task-1")
    process_video("task-1", str(source_file))

    mock_pipeline["requests"].post.assert_called_once()

    # Confirm the call is using multipart file upload
    _, call_kwargs = mock_pipeline["requests"].post.call_args
    assert "files" in call_kwargs
    assert "audio_file" in call_kwargs["files"]
    assert "json" not in call_kwargs

    mock_pipeline["ollama"].chat.assert_called_once()

    r = mock_pipeline["redis"]
    r.setex.assert_any_call(
        "result:task-1", 3600,
        json.dumps({"status": "completed", "task_id": "task-1", "summary": "a short summary"})
        )
    r.publish.assert_called_once()


def test_duration(mock_pipeline, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()

    mock_pipeline['ffmpeg'].probe.return_value = {"format": {"duration": str(50 * 60)}}

    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"fake bytes")

    with pytest.raises(ValueError, match="Audio file duration exceeds 30 minutes limit: 50.00 minutes"):
        process_video("task-2", str(source_file))


    mock_pipeline["requests"].post.assert_not_called() # Should not call
    mock_pipeline["ollama"].chat.assert_not_called()

    r = mock_pipeline["redis"]
    r.setex.assert_any_call(
            "result:task-2", 3600,
            json.dumps({
                "status": "error",
                "task_id": "task-2",
                "error": "Audio file duration exceeds 30 minutes limit: 50.00 minutes"
            })
    )
    r.publish.assert_called_once()

def test_whisper_fail(mock_pipeline, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()

    mock_pipeline["requests"].post.return_value.status_code = 500
    mock_pipeline["requests"].post.return_value.text = "Internal Server Error"

    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"fake bytes")

    write_fake_audio("task-1")
    with pytest.raises(ValueError, match="Whisper service error: 500: Internal Server Error"):
        process_video("task-1", str(source_file))

    mock_pipeline["ollama"].chat.assert_not_called()

    r = mock_pipeline["redis"]
    r.setex.assert_any_call(
            "result:task-1", 3600,
            json.dumps({
                "status": "error",
                "task_id": "task-1",
                "error": "Whisper service error: 500: Internal Server Error"
            })
    )
    r.publish.assert_called_once()

