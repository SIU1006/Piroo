from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from model_eval.provenance import load_config, revision_for
from worker.canary import check_canary_wer
from worker.metrics import CANARY_LAST_SUCCESS_UNIX_SECONDS, CANARY_WER


def test_canary_posts_wav_only_to_candidate_and_records_success(monkeypatch):
    monkeypatch.setattr("worker.canary.CANARY_WHISPER_URL", "http://candidate:3000")
    monkeypatch.setattr("worker.canary.CANARY_WHISPER_IMAGE", "whisper@sha256:" + "a" * 64)
    clip = {
        "filename": "clip_00.wav",
        "reference_text": "known transcript",
    }
    response = Mock(text="known transcript")
    metadata = Mock()
    metadata.json.return_value = {"model_size": "tiny", "model_revision": revision_for("tiny"),
                                  "transcribe": load_config()["transcribe"]}
    response.raise_for_status.return_value = None
    with patch("worker.canary._load_canary_manifest", return_value=[clip]), \
         patch("worker.canary.redis.Redis.from_url", return_value=MagicMock()), \
         patch("worker.canary.requests.post", side_effect=[metadata, response]) as post:
        before = CANARY_LAST_SUCCESS_UNIX_SECONDS._value.get()
        assert check_canary_wer.run() == 0

    _, kwargs = post.call_args
    name, payload, content_type = kwargs["files"]["audio_file"]
    assert name == "clip_00.wav"
    assert content_type == "audio/wav"
    audio = Path(payload.name).read_bytes()
    assert audio.startswith(b"RIFF")
    assert [call.args[0] for call in post.call_args_list] == [
        "http://candidate:3000/metadata", "http://candidate:3000/transcribe",
    ]
    response.json.assert_not_called()
    assert len(audio) > 100
    assert CANARY_WER._value.get() == 0
    assert CANARY_LAST_SUCCESS_UNIX_SECONDS._value.get() > before


def test_failed_canary_does_not_advance_success_timestamp(monkeypatch):
    monkeypatch.setattr("worker.canary.CANARY_WHISPER_URL", "http://candidate:3000")
    monkeypatch.setattr("worker.canary.CANARY_WHISPER_IMAGE", "whisper@sha256:" + "a" * 64)
    clip = {"filename": "clip_00.wav", "reference_text": "known transcript"}
    with patch("worker.canary._load_canary_manifest", return_value=[clip]), \
         patch("worker.canary.requests.post", side_effect=TimeoutError("service offline")):
        before = CANARY_LAST_SUCCESS_UNIX_SECONDS._value.get()
        with pytest.raises(TimeoutError):
            check_canary_wer.run()
        assert CANARY_LAST_SUCCESS_UNIX_SECONDS._value.get() == before


def test_disabled_candidate_does_not_call_shared_service(monkeypatch):
    monkeypatch.setattr("worker.canary.CANARY_WHISPER_URL", "")
    with patch("worker.canary.requests.post") as post:
        assert check_canary_wer.run() is None
    post.assert_not_called()
