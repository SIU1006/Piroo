import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from model_eval.provenance import dataset_identity, load_config, require_comparable


def test_audio_or_reference_changes_invalidate_baseline(tmp_path):
    config = load_config()
    config["filenames"] = ["a.wav"]
    (tmp_path / "a.wav").write_bytes(b"audio")
    manifest = [{"filename": "a.wav", "reference_text": "hello", "source_id": "a"}]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    before, _ = dataset_identity(tmp_path, config)
    (tmp_path / "a.wav").write_bytes(b"different audio")
    after, _ = dataset_identity(tmp_path, config)
    with pytest.raises(ValueError, match="dataset_sha256"):
        require_comparable({"evaluation": before}, {"evaluation": after})
    (tmp_path / "a.wav").write_bytes(b"audio")
    manifest[0]["reference_text"] = "changed reference"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    after, _ = dataset_identity(tmp_path, config)
    with pytest.raises(ValueError):
        require_comparable({"evaluation": before}, {"evaluation": after})


def test_subset_and_decoding_changes_are_incomparable(tmp_path):
    config = load_config()
    manifest = [{"filename": f"{name}.wav", "reference_text": name} for name in ("a", "b")]
    for clip in manifest:
        (tmp_path / clip["filename"]).write_bytes(b"audio")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    config["filenames"] = ["a.wav"]
    before, _ = dataset_identity(tmp_path, config)
    config["filenames"] = ["b.wav"]
    after, _ = dataset_identity(tmp_path, config)
    with pytest.raises(ValueError, match="subset_sha256"):
        require_comparable({"evaluation": before}, {"evaluation": after})
    config["filenames"] = ["a.wav"]
    config["transcribe"]["beam_size"] = 1
    after, _ = dataset_identity(tmp_path, config)
    with pytest.raises(ValueError, match="config_sha256"):
        require_comparable({"evaluation": before}, {"evaluation": after})


def test_promotion_rejects_fresh_failure_despite_good_logged_metrics(tmp_path, monkeypatch):
    from model_eval import registry

    identity = {"dataset_sha256": "dataset", "subset_sha256": "subset",
                "config_sha256": "config", "config": load_config()}
    logged = {"evaluation": identity, "wer": 0.01, "rtf": 0.1, "model_size": "base",
              "model_revision": "revision", "deployed_image": "sha256:image"}
    baseline = {**logged, "version": "1"}
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))
    client = MagicMock()
    client.get_model_version_by_alias.return_value = SimpleNamespace(run_id="run", version="2")
    monkeypatch.setattr(registry, "MlflowClient", lambda: client)
    monkeypatch.setattr(registry.mlflow.artifacts, "load_dict", lambda uri: logged)
    fresh_benchmark = MagicMock(return_value={**logged, "wer": 0.9})
    monkeypatch.setattr(registry, "benchmark_model", fresh_benchmark)
    with pytest.raises(ValueError, match="budget"):
        registry.promote("model", "http://candidate", "sha256:image", baseline_path)
    assert fresh_benchmark.call_args.kwargs["endpoint"] == "http://candidate"
    client.set_registered_model_alias.assert_not_called()
    assert json.loads(baseline_path.read_text()) == baseline
