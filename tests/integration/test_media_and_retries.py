import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from worker.tasks import TransientServiceError, extract_audio, process_video

pytestmark = pytest.mark.integration


def make_mp3(path: Path, duration: float = 0.25) -> None:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            f"sine=frequency=1000:duration={duration}", "-ac", "2", str(path),
        ],
        check=True,
    )


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["streams"][0]


def test_real_mp3_extraction_preserves_source_and_outputs_mono_mp3(tmp_path):
    source = tmp_path / "source.mp3"
    output = tmp_path / "output.audio.mp3"
    make_mp3(source)
    original = source.read_bytes()

    extract_audio(str(source), str(output))

    stream = probe(output)
    assert source.read_bytes() == original
    assert output != source
    assert stream["codec_name"] == "mp3"
    assert stream["channels"] == 1
    assert output.stat().st_size > 0


def test_celery_retry_recovers_and_publishes_one_terminal_result(
    redis_client, tmp_path, monkeypatch
):
    monkeypatch.setattr("worker.tasks.UPLOAD_DIR", tmp_path)
    source = tmp_path / "recover.mp3"
    make_mp3(source)

    with patch(
        "worker.tasks.transcribe",
        side_effect=[TransientServiceError("temporary outage"), "recovered transcript"],
    ) as transcribe, patch("worker.tasks.summarize", return_value="recovered summary"):
        result = process_video.apply(args=("retry-recovery", str(source)), throw=False)

    assert result.successful()
    assert transcribe.call_count == 2
    assert json.loads(redis_client.get("result:retry-recovery")) == {
        "status": "completed",
        "task_id": "retry-recovery",
        "summary": "recovered summary",
    }
    assert not source.exists()


def test_celery_retry_exhaustion_publishes_one_failure(redis_client, tmp_path, monkeypatch):
    monkeypatch.setattr("worker.tasks.UPLOAD_DIR", tmp_path)
    source = tmp_path / "exhaust.mp3"
    make_mp3(source)

    with patch(
        "worker.tasks.transcribe", side_effect=TransientServiceError("still unavailable")
    ) as transcribe:
        result = process_video.apply(args=("retry-exhausted", str(source)), throw=False)

    assert result.failed()
    assert transcribe.call_count == process_video.max_retries + 1
    payload = json.loads(redis_client.get("result:retry-exhausted"))
    assert payload["status"] == "error"
    assert payload["task_id"] == "retry-exhausted"
    assert "still unavailable" in payload["error"]
    assert not source.exists()
