import json
import math
import os
import subprocess
from pathlib import Path

import pytest
import requests

from worker import canary
from worker.metrics import CANARY_LAST_SUCCESS_UNIX_SECONDS
from worker.tasks import transcribe

pytestmark = pytest.mark.integration


def test_bentoml_returns_plain_text_consumed_by_worker_and_canary(tmp_path, monkeypatch):
    whisper_url = os.environ["WHISPER_URL"]
    # This disposable service is the candidate-only endpoint in this topology.
    monkeypatch.setattr(canary, "CANARY_WHISPER_URL", whisper_url)
    monkeypatch.setattr(canary, "CANARY_WHISPER_IMAGE",
                        "whisper@" + os.environ["WHISPER_TEST_IMAGE_DIGEST"])
    source_wav = Path("worker/canary_clips/clip_00.wav").resolve()
    worker_mp3 = tmp_path / "contract.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(source_wav), str(worker_mp3)],
        check=True,
    )

    with worker_mp3.open("rb") as audio:
        raw = requests.post(
            f"{whisper_url}/transcribe",
            files={"audio_file": (worker_mp3.name, audio, "audio/mpeg")},
            timeout=120,
        )
    raw.raise_for_status()
    assert raw.headers["content-type"].startswith("text/plain")
    with pytest.raises(json.JSONDecodeError):
        raw.json()

    worker_transcript = transcribe(str(worker_mp3), whisper_url)
    assert worker_transcript == raw.text

    monkeypatch.setattr(canary, "CANARY_DIR", source_wav.parent)
    monkeypatch.setattr(
        canary,
        "_load_canary_manifest",
        lambda: [{"filename": source_wav.name, "reference_text": worker_transcript}],
    )
    before = CANARY_LAST_SUCCESS_UNIX_SECONDS._value.get()
    canary_wer = canary.check_canary_wer.run()
    assert math.isfinite(canary_wer)
    assert 0 <= canary_wer <= 1
    assert CANARY_LAST_SUCCESS_UNIX_SECONDS._value.get() > before
