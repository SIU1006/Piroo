"""Content identities shared by CI, registration, promotion and inference."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parent
DEFAULT_CONFIG = ROOT / "evaluation_config.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def revision_for(size):
    return json.loads((ROOT / "model_revisions.json").read_text())[size]


def load_config(path=DEFAULT_CONFIG):
    config = json.loads(Path(path).read_text())
    names = config["filenames"]
    if not names or len(names) != len(set(names)):
        raise ValueError("Evaluation filenames must be nonempty and unique")
    if config["normalization"] != "lowercase_remove_punctuation_collapse_spaces_v1":
        raise ValueError("Unsupported WER normalization")
    return config


def dataset_identity(directory, config):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    records = []
    for clip in manifest:
        path = (directory / clip["filename"]).resolve()
        if path.parent != directory.resolve():
            raise ValueError("Media must be directly inside the evaluation directory")
        records.append({**clip, "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    by_name = {clip["filename"]: clip for clip in records}
    if len(by_name) != len(records):
        raise ValueError("Duplicate manifest filenames")
    selected = [by_name[name] for name in config["filenames"]]
    return {
        "dataset_sha256": digest(records), "subset_sha256": digest(selected),
        "examples": [{"filename": clip["filename"], "source_id": clip.get("source_id"),
                      "audio_sha256": clip["audio_sha256"]} for clip in selected],
        "config_sha256": digest(config), "config": config,
    }, selected


def require_comparable(baseline, candidate):
    for key in ("dataset_sha256", "subset_sha256", "config_sha256"):
        if baseline["evaluation"][key] != candidate["evaluation"][key]:
            raise ValueError(f"Incomparable evaluations: {key} differs")
