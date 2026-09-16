"""One pinned evaluation contract for local and deployed model comparisons."""
import importlib.metadata
import re
import time
from pathlib import Path

import jiwer
import requests

from model_eval.provenance import (
    DEFAULT_CONFIG,
    ROOT,
    dataset_identity,
    load_config,
    revision_for,
)

EVAL_DIR = ROOT / "eval_data"
_WER_TRANSFORM = jiwer.Compose([
    jiwer.ToLowerCase(), jiwer.RemovePunctuation(), jiwer.RemoveMultipleSpaces(),
    jiwer.Strip(), jiwer.ReduceToListOfListOfWords(),
])


def benchmark_model(model_size, config_path=DEFAULT_CONFIG, endpoint=None,
                    deployed_image="not-deployed", revision=None, eval_dir=EVAL_DIR):
    config = load_config(config_path)
    identity, manifest = dataset_identity(eval_dir, config)
    revision = revision or revision_for(model_size)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Model revision must be an immutable 40-character commit")
    started = time.perf_counter()
    if endpoint:
        metadata = requests.post(f"{endpoint}/metadata", timeout=30).json()
        expected = {"model_size": model_size, "model_revision": revision,
                    "transcribe": config["transcribe"], "device": config["device"],
                    "compute_type": config["compute_type"]}
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise ValueError("Live model metadata does not match the evaluation contract")
        if not re.fullmatch(r"(?:[^\s]+@)?sha256:[0-9a-f]{64}", deployed_image):
            raise ValueError("Deployed evaluation requires an immutable image digest")
        packages = metadata["packages"]
    else:
        from faster_whisper import WhisperModel
        from huggingface_hub import snapshot_download

        path = snapshot_download(f"Systran/faster-whisper-{model_size}", revision=revision)
        model = WhisperModel(path, device=config["device"], compute_type=config["compute_type"])
        packages = {name: importlib.metadata.version(name) for name in
                    ("faster-whisper", "ctranslate2", "jiwer")}
    load_seconds = time.perf_counter() - started
    references, hypotheses, per_clip = [], [], []
    for clip in manifest:
        audio_path = Path(eval_dir) / clip["filename"]
        started = time.perf_counter()
        if endpoint:
            with audio_path.open("rb") as stream:
                response = requests.post(f"{endpoint}/transcribe",
                                         files={"audio_file": (clip["filename"], stream, "audio/wav")},
                                         timeout=600)
            response.raise_for_status()
            if not response.headers.get("content-type", "").startswith("text/plain"):
                raise ValueError("Whisper transcription contract must be text/plain")
            hypothesis = response.text
        else:
            segments, _ = model.transcribe(str(audio_path), **config["transcribe"])
            hypothesis = " ".join(segment.text for segment in segments)
        seconds = time.perf_counter() - started
        references.append(clip["reference_text"])
        hypotheses.append(hypothesis)
        per_clip.append({
            "filename": clip["filename"], "reference": clip["reference_text"],
            "hypothesis": hypothesis, "audio_seconds": clip["duration_seconds"],
            "transcribe_seconds": seconds,
            "wer": jiwer.wer(clip["reference_text"], hypothesis,
                             reference_transform=_WER_TRANSFORM, hypothesis_transform=_WER_TRANSFORM),
        })
    audio_seconds = sum(clip["audio_seconds"] for clip in per_clip)
    transcribe_seconds = sum(clip["transcribe_seconds"] for clip in per_clip)
    return {
        "schema_version": 2, "model_size": model_size, "model_revision": revision,
        "deployed_image": deployed_image, "packages": packages, "evaluation": identity,
        "device": config["device"], "compute_type": config["compute_type"],
        "load_seconds": load_seconds, "n_clips": len(per_clip),
        "total_audio_seconds": audio_seconds, "total_transcribe_seconds": transcribe_seconds,
        "rtf": transcribe_seconds / audio_seconds, "evaluated_at": time.time(),
        "wer": jiwer.wer(references, hypotheses, reference_transform=_WER_TRANSFORM,
                         hypothesis_transform=_WER_TRANSFORM), "per_clip": per_clip,
    }
