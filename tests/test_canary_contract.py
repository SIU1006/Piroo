import json
from unittest.mock import MagicMock

from model_eval.provenance import load_config, revision_for
from worker import canary


def test_disabled_candidate_does_not_fall_back_to_shared_service(monkeypatch):
    monkeypatch.setattr(canary, "CANARY_WHISPER_URL", "")
    post = MagicMock()
    monkeypatch.setattr(canary.requests, "post", post)
    assert canary.check_canary_wer() is None
    post.assert_not_called()


def test_canary_uses_candidate_only_plain_text_and_records_identity(monkeypatch):
    manifest = json.loads(canary.CANARY_MANIFEST.read_text())[:1]
    monkeypatch.setattr(canary, "_load_canary_manifest", lambda: manifest)
    monkeypatch.setattr(canary, "CANARY_WHISPER_URL", "http://candidate-only:3000")
    monkeypatch.setattr(canary, "CANARY_WHISPER_IMAGE", "registry/whisper@sha256:" + "a" * 64)
    metadata = MagicMock()
    metadata.json.return_value = {"model_size": "base", "model_revision": revision_for("base"),
                                  "transcribe": load_config()["transcribe"]}
    transcript = MagicMock()
    transcript.text = manifest[0]["reference_text"]
    transcript.json.side_effect = ValueError("Plain text cannot be parsed as JSON")
    post = MagicMock(side_effect=[metadata, transcript])
    monkeypatch.setattr(canary.requests, "post", post)
    client = MagicMock()
    monkeypatch.setattr(canary.redis.Redis, "from_url", lambda url: client)
    assert canary.check_canary_wer() == 0
    assert all(call.args[0].startswith("http://candidate-only:3000/")
               for call in post.call_args_list)
    transcript.json.assert_not_called()
    report = json.loads(client.__enter__.return_value.setex.call_args.args[2])
    assert report["deployed_image"].endswith("a" * 64)
    assert report["evaluation"]["examples"][0]["filename"] == manifest[0]["filename"]
