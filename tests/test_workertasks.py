import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import ollama
import pytest
import redis

from settings import UPLOAD_DIR
from worker.tasks import (
    HEARTBEAT_TTL_SECONDS,
    STUCK_DEADLINE_TTL_SECONDS,
    TASK_HARD_TIME_LIMIT_SECONDS,
    TransientServiceError,
    cleanup_stuck,
    extract_audio,
    extracted_audio_path,
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
        patch("worker.tasks.ollama.Client") as mock_ollama, \
        patch("worker.tasks.redis") as mock_redis:

        mock_ffmpeg.probe.return_value = {"format": {"duration": 120.0}}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = "fake transcript"
        mock_requests.post.return_value = mock_response

        mock_ollama_client = MagicMock()
        mock_ollama_client.chat.return_value.message.content = "a short summary"

        mock_ollama.return_value = mock_ollama_client

        mock_redis_instance = MagicMock()
        mock_redis_instance.eval.return_value = 1
        mock_redis.Redis.from_url.return_value = mock_redis_instance

        yield {
            "ffmpeg": mock_ffmpeg,
            "requests": mock_requests,
            "ollama": mock_ollama_client,
            "redis": mock_redis_instance
        }

def write_fake_audio(task_id: str) -> None:
    audio_path = UPLOAD_DIR / f"{task_id}.audio.mp3"
    audio_path.write_bytes(b"fake audio")


def test_mp3_extraction_keeps_source_and_writes_distinct_audio(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")

    source_file = tmp_path / "task-1.mp3"
    audio_file = tmp_path / "task-1.audio.mp3"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=1000:duration=1", str(source_file),
        ],
        check=True,
    )
    original_bytes = source_file.read_bytes()

    extract_audio(str(source_file), str(audio_file))

    assert audio_file.exists()
    assert source_file.read_bytes() == original_bytes


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
    r.delete.assert_any_call("heartbeat:task-1")


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
    with pytest.raises(TransientServiceError, match="Whisper service error: 500"):
        process_video.run.__wrapped__("task-1", str(source_file))

    mock_pipeline["ollama"].chat.assert_not_called()

    r = mock_pipeline["redis"]
    r.publish.assert_not_called()
    assert source_file.exists()
    assert not (UPLOAD_DIR / "task-1.audio.mp3").exists()
    assert call("heartbeat:task-1") not in r.delete.call_args_list


def test_transient_whisper_failure_recovers_on_next_attempt(mock_pipeline, tmp_path):
    source_file = tmp_path / "task-1.mp3"
    source_file.write_bytes(b"source audio")
    assert extracted_audio_path("task-1") != str(source_file)
    write_fake_audio("task-1")

    response = mock_pipeline["requests"].post.return_value
    response.status_code = 503
    response.text = "unavailable"
    with pytest.raises(TransientServiceError):
        process_video.run.__wrapped__("task-1", str(source_file))

    assert source_file.exists()
    assert mock_pipeline["redis"].publish.call_count == 0

    response.status_code = 200
    response.text = "transcript"
    write_fake_audio("task-1")
    process_video.run.__wrapped__("task-1", str(source_file))

    assert mock_pipeline["redis"].publish.call_count == 1
    assert not source_file.exists()
    mock_pipeline["redis"].delete.assert_any_call("heartbeat:task-1")


def test_exhausted_whisper_retry_publishes_one_failure(mock_pipeline, tmp_path):
    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"source video")
    write_fake_audio("task-2")
    response = mock_pipeline["requests"].post.return_value
    response.status_code = 503
    response.text = "unavailable"

    process_video.push_request(retries=process_video.max_retries)
    try:
        with pytest.raises(TransientServiceError):
            process_video.run.__wrapped__("task-2", str(source_file))
    finally:
        process_video.pop_request()

    published = json.loads(mock_pipeline["redis"].publish.call_args.args[1])
    assert published["status"] == "error"
    assert published["task_id"] == "task-2"
    assert mock_pipeline["redis"].publish.call_count == 1
    assert not source_file.exists()


def test_celery_autoretry_recovers_after_transient_whisper_error(mock_pipeline, tmp_path):
    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"source video")
    unavailable = MagicMock(status_code=503, text="unavailable")
    recovered = MagicMock(status_code=200, text="transcript")
    mock_pipeline["requests"].post.side_effect = [unavailable, recovered]

    with patch("worker.tasks.extract_audio") as mock_extract:
        mock_extract.side_effect = lambda _source, audio: Path(audio).write_bytes(b"extracted audio")
        result = process_video.apply(args=("task-5", str(source_file)), throw=False)

    assert result.successful()
    assert mock_pipeline["requests"].post.call_count == 2
    assert mock_pipeline["redis"].publish.call_count == 1
    assert not source_file.exists()


def test_redis_outage_before_processing_preserves_source_for_retry(mock_pipeline, tmp_path):
    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"source video")
    mock_pipeline["redis"].eval.side_effect = redis.exceptions.ConnectionError("Redis down")

    with pytest.raises(redis.exceptions.ConnectionError):
        process_video.run.__wrapped__("task-6", str(source_file))

    assert source_file.exists()
    mock_pipeline["redis"].publish.assert_not_called()


def test_existing_sweeper_claim_prevents_attempt_and_preserves_files(mock_pipeline, tmp_path):
    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"source video")
    mock_pipeline["redis"].eval.return_value = 0

    process_video.run.__wrapped__("task-claimed", str(source_file))

    assert source_file.exists()
    mock_pipeline["ffmpeg"].probe.assert_not_called()
    mock_pipeline["redis"].delete.assert_not_called()


def test_terminal_result_redis_failure_preserves_source_for_retry(mock_pipeline, tmp_path):
    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"source video")
    mock_pipeline["ffmpeg"].probe.return_value = {"format": {"duration": str(50 * 60)}}
    mock_pipeline["redis"].setex.side_effect = redis.exceptions.ConnectionError("Redis down")

    with pytest.raises(redis.exceptions.ConnectionError):
        process_video.run.__wrapped__("task-7", str(source_file))

    assert source_file.exists()
    mock_pipeline["redis"].delete.assert_any_call("active:task-7")
    assert call("heartbeat:task-7") not in mock_pipeline["redis"].delete.call_args_list


def test_ollama_503_retries_without_final_error(mock_pipeline, tmp_path):
    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"source video")
    write_fake_audio("task-3")
    mock_pipeline["ollama"].chat.side_effect = ollama.ResponseError("unavailable", 503)

    with pytest.raises(TransientServiceError, match="Ollama service error"):
        process_video.run.__wrapped__("task-3", str(source_file))

    assert source_file.exists()
    mock_pipeline["redis"].publish.assert_not_called()


def test_ollama_400_does_not_retry(mock_pipeline, tmp_path):
    source_file = tmp_path / "input.mp4"
    source_file.write_bytes(b"source video")
    write_fake_audio("task-4")
    mock_pipeline["ollama"].chat.side_effect = ollama.ResponseError("bad request", 400)

    with pytest.raises(ollama.ResponseError):
        process_video.run.__wrapped__("task-4", str(source_file))

    assert not source_file.exists()
    assert mock_pipeline["redis"].publish.call_count == 1


# ---------------------------------------------------------------------------
# find_stuck
# ---------------------------------------------------------------------------

def test_find_stuck_yields_only_expired_without_result():
    r = MagicMock()
    r.scan_iter.return_value = [b"heartbeat:task-1", b"heartbeat:task-2", b"heartbeat:task-3"]
    r.eval.side_effect = [0, 0, 1]

    with patch("worker.tasks.uuid.uuid4") as mock_uuid:
        mock_uuid.side_effect = [
            MagicMock(hex="claim-1"),
            MagicMock(hex="claim-2"),
            MagicMock(hex="claim-3"),
        ]
        stuck = list(find_stuck(r))

    assert stuck == [("task-3", "heartbeat:task-3", "claim-3")]
    assert r.eval.call_args.args[2:6] == (
        "result:task-3",
        "active:task-3",
        "heartbeat:task-3",
        "sweeper-claim:task-3",
    )
    assert r.eval.call_args.args[6] == STUCK_DEADLINE_TTL_SECONDS


def test_find_stuck_yields_nothing_when_none_are_stuck():
    r = MagicMock()
    r.scan_iter.return_value = [b"heartbeat:task-1"]
    r.eval.return_value = 0

    assert list(find_stuck(r)) == []


def test_stuck_sweep_waits_until_after_hard_deadline():
    r = MagicMock()
    r.scan_iter.return_value = [b"heartbeat:task-1"]
    assert HEARTBEAT_TTL_SECONDS - STUCK_DEADLINE_TTL_SECONDS > TASK_HARD_TIME_LIMIT_SECONDS
    r.eval.return_value = 0
    assert list(find_stuck(r)) == []
    assert r.eval.call_args.args[6] == STUCK_DEADLINE_TTL_SECONDS


def test_find_stuck_skips_a_live_worker_after_deadline():
    r = MagicMock()
    r.scan_iter.return_value = [b"heartbeat:task-1"]
    r.eval.return_value = 0

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

    audio_path = UPLOAD_DIR / "task-3.audio.mp3"
    audio_path.write_bytes(b"y")

    r = MagicMock()
    r.eval.return_value = f"running:{original_file}".encode()

    cleanup_stuck(r, "task-3", "heartbeat:task-3", "claim-3")

    assert r.eval.call_args.args[2:] == (
        "heartbeat:task-3", "sweeper-claim:task-3", "claim-3"
    )
    assert not original_file.exists()
    assert not audio_path.exists()


def test_cleanup_stuck_without_original_path(tmp_path, monkeypatch):
    """If the heartbeat value is missing/empty, only the audio path should
    be cleaned up — no crash from a missing original_path."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()

    audio_path = UPLOAD_DIR / "task-4.audio.mp3"
    audio_path.write_bytes(b"y")

    r = MagicMock()
    r.eval.return_value = b"running:"

    cleanup_stuck(r, "task-4", "heartbeat:task-4", "claim-4")

    assert not audio_path.exists()


def test_cleanup_stuck_without_owned_claim_keeps_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    audio_path = UPLOAD_DIR / "task-4.audio.mp3"
    audio_path.write_bytes(b"y")
    r = MagicMock()
    r.eval.return_value = None

    cleanup_stuck(r, "task-4", "heartbeat:task-4", "lost-claim")

    assert audio_path.exists()


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
        mock_redis_instance.exists.return_value = False

        mock_find_stuck.return_value = [
            ("task-1", "heartbeat:task-1", "claim-1"),
            ("task-2", "heartbeat:task-2", "claim-2"),
        ]

        sweep_stuck_tasks()

        mock_find_stuck.assert_called_once_with(mock_redis_instance)
        mock_report_stuck.assert_has_calls([
            call(mock_redis_instance, "task-1"),
            call(mock_redis_instance, "task-2"),
        ])
        mock_cleanup_stuck.assert_has_calls([
            call(mock_redis_instance, "task-1", "heartbeat:task-1", "claim-1"),
            call(mock_redis_instance, "task-2", "heartbeat:task-2", "claim-2"),
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


def test_sweep_only_processes_tasks_returned_with_a_claim():
    with patch("worker.tasks.redis") as mock_redis, \
        patch("worker.tasks.find_stuck", return_value=[]), \
        patch("worker.tasks.report_stuck") as mock_report_stuck, \
        patch("worker.tasks.cleanup_stuck") as mock_cleanup_stuck:
        r = MagicMock()
        mock_redis.Redis.from_url.return_value = r

        sweep_stuck_tasks()

    mock_report_stuck.assert_not_called()
    mock_cleanup_stuck.assert_not_called()
