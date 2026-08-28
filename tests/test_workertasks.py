import json
from unittest.mock import MagicMock, call, patch

import pytest

from settings import UPLOAD_DIR
from worker.tasks import (
    cleanup_stuck,
    find_stuck,
    process_video,
    report_stuck,
    sweep_stuck_tasks,
)

'''
Unit testing all external dependencies (ffmpeg, requests, ollama, redis).

This tests process_video's logic:
- duration, validation, error handling, what gets published.

Also tests the sweep_stuck_tasks pipeline (find_stuck, report_stuck,
cleanup_stuck, sweep_stuck_tasks itself).

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


# ---------------------------------------------------------------------------
# process_video
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# find_stuck
# ---------------------------------------------------------------------------

def test_find_stuck_yields_only_expired_without_result():
    r = MagicMock()
    r.scan_iter.return_value = [b"heartbeat:task-1", b"heartbeat:task-2", b"heartbeat:task-3"]

    # task-1: has a result already -> not stuck
    # task-2: no result, ttl still high -> not stuck yet
    # task-3: no result, ttl low -> stuck
    
    def exists_side_effect(key):
        return key == "result:task-1"

    def ttl_side_effect(key):
        return {b"heartbeat:task-2": 500, b"heartbeat:task-3": 10}[key]

    r.exists.side_effect = exists_side_effect
    r.ttl.side_effect = ttl_side_effect

    stuck = list(find_stuck(r))

    assert stuck == [("task-3", b"heartbeat:task-3")]


def test_find_stuck_yields_nothing_when_none_are_stuck():
    r = MagicMock()
    r.scan_iter.return_value = [b"heartbeat:task-1"]
    r.exists.return_value = False
    r.ttl.return_value = 1000  # plenty of time left

    assert list(find_stuck(r)) == []


# ---------------------------------------------------------------------------
# report_stuck
# ---------------------------------------------------------------------------

def test_report_stuck_publishes_expected_payload():
    r = MagicMock()
    with patch("worker.tasks.publish_result") as mock_publish:
        report_stuck(r, "task-3")

    mock_publish.assert_called_once_with(r, "task-3", {
        "status": "error",
        "task_id": "task-3",
        "error": "Task did not finish in time and was stopped.",
    })


# ---------------------------------------------------------------------------
# cleanup_stuck
# ---------------------------------------------------------------------------

def test_cleanup_stuck_deletes_heartbeat_and_removes_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()

    original_file = tmp_path / "original_input.mp4"
    original_file.write_bytes(b"x")

    audio_path = UPLOAD_DIR / "task-3.mp3"
    audio_path.write_bytes(b"y")

    r = MagicMock()
    r.get.return_value = f"running:{original_file}".encode()

    cleanup_stuck(r, "task-3", "heartbeat:task-3")

    r.delete.assert_called_once_with("heartbeat:task-3")
    assert not original_file.exists()
    assert not audio_path.exists()


def test_cleanup_stuck_without_original_path(tmp_path, monkeypatch):
    """If the heartbeat value is missing/empty, only the audio path should
    be cleaned up — no crash from a missing original_path."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()

    audio_path = UPLOAD_DIR / "task-4.mp3"
    audio_path.write_bytes(b"y")

    r = MagicMock()
    r.get.return_value = None

    cleanup_stuck(r, "task-4", "heartbeat:task-4")

    r.delete.assert_called_once_with("heartbeat:task-4")
    assert not audio_path.exists()


# ---------------------------------------------------------------------------
# sweep_stuck_tasks — wires find_stuck -> report_stuck -> cleanup_stuck
# ---------------------------------------------------------------------------

def test_sweep_stuck_tasks_processes_each_stuck_task():
    with patch("worker.tasks.redis") as mock_redis, \
        patch("worker.tasks.find_stuck") as mock_find_stuck, \
        patch("worker.tasks.report_stuck") as mock_report_stuck, \
        patch("worker.tasks.cleanup_stuck") as mock_cleanup_stuck:

        mock_redis_instance = MagicMock()
        mock_redis.Redis.from_url.return_value = mock_redis_instance

        mock_find_stuck.return_value = [
            ("task-1", "heartbeat:task-1"),
            ("task-2", "heartbeat:task-2"),
        ]

        sweep_stuck_tasks()

        mock_find_stuck.assert_called_once_with(mock_redis_instance)
        mock_report_stuck.assert_has_calls([
            call(mock_redis_instance, "task-1"),
            call(mock_redis_instance, "task-2"),
        ])
        mock_cleanup_stuck.assert_has_calls([
            call(mock_redis_instance, "task-1", "heartbeat:task-1"),
            call(mock_redis_instance, "task-2", "heartbeat:task-2"),
        ])


def test_sweep_stuck_tasks_does_nothing_when_no_stuck_tasks():
    with patch("worker.tasks.redis") as mock_redis, \
        patch("worker.tasks.find_stuck") as mock_find_stuck, \
        patch("worker.tasks.report_stuck") as mock_report_stuck, \
        patch("worker.tasks.cleanup_stuck") as mock_cleanup_stuck:

        mock_redis.Redis.from_url.return_value = MagicMock()
        mock_find_stuck.return_value = []

        sweep_stuck_tasks()

        mock_report_stuck.assert_not_called()
        mock_cleanup_stuck.assert_not_called()