from unittest.mock import Mock, patch

import pytest

from worker.canary import check_canary_wer
from worker.metrics import CANARY_LAST_SUCCESS_UNIX_SECONDS, CANARY_WER


def test_canary_posts_mp3_and_records_success_timestamp():
    clip = {
        "filename": "clip_00.wav",
        "reference_text": "known transcript",
    }
    response = Mock(text="known transcript")
    response.raise_for_status.return_value = None
    with patch("worker.canary._load_canary_manifest", return_value=[clip]), \
         patch("worker.canary.requests.post", return_value=response) as post:
        before = CANARY_LAST_SUCCESS_UNIX_SECONDS._value.get()
        assert check_canary_wer.run() == 0

    _, kwargs = post.call_args
    name, payload, content_type = kwargs["files"]["audio_file"]
    assert name == "clip_00.mp3"
    assert content_type == "audio/mpeg"
    assert payload.startswith((b"ID3", b"\xff\xfb"))
    assert len(payload) > 100
    assert CANARY_WER._value.get() == 0
    assert CANARY_LAST_SUCCESS_UNIX_SECONDS._value.get() > before


def test_failed_canary_does_not_advance_success_timestamp():
    clip = {"filename": "clip_00.wav", "reference_text": "known transcript"}
    with patch("worker.canary._load_canary_manifest", return_value=[clip]), \
         patch("worker.canary.requests.post", side_effect=TimeoutError("service offline")):
        before = CANARY_LAST_SUCCESS_UNIX_SECONDS._value.get()
        with pytest.raises(TimeoutError):
            check_canary_wer.run()
        assert CANARY_LAST_SUCCESS_UNIX_SECONDS._value.get() == before
