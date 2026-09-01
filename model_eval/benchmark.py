import json
import time
from pathlib import Path

import jiwer
from faster_whisper import WhisperModel

'''
Core Harness (not run directly; imported by the others).
1. Loads one whisper model size
2. Transcribes every clip in eval_data/
3. Computes real WER (vs. the reference transcripts) and RTF (latency).
'''


'''Normalize both reference and hypothesis before diffing so WER reflects real transcription errors, not casing/punctuation noise faster-whisper doesn't even try to match.'''

EVAL_DIR = Path(__file__).parent / "eval_data"
MANIFEST_PATH = EVAL_DIR / "manifest.json"

_WER_TRANSFORM = jiwer.Compose(
    [
        jiwer.ToLowerCase(),
        jiwer.RemovePunctuation(),
        jiwer.RemoveMultipleSpaces(),
        jiwer.Strip(),
        jiwer.ReduceToListOfListOfWords(),
    ]
)

def load_manifest(max_clips: int | None = None) -> list[dict]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"{MANIFEST_PATH} not found. Run `python model_eval/prepare_eval_set.py` first to build the fixed eval set."
        )

    manifest = json.loads(MANIFEST_PATH.read_text())
    if max_clips is not None:
        manifest = manifest[:max_clips]
    return manifest

def benchmark_model(model_size: str, device: str = "cpu", compute_type: str = "int8", max_clips: int | None = None) -> dict:

    manifest = load_manifest(max_clips=max_clips)

    load_start = time.perf_counter()
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    load_seconds = time.perf_counter() - load_start

    references, hypotheses, per_clip = [], [], []
    total_audio_seconds = 0.0
    total_transcribe_seconds = 0.0

    # transcribe every clip in eval set
    for clip in manifest:
        audio_path = EVAL_DIR / clip["filename"]

        transcribe_start = time.perf_counter()
        segments, info = model.transcribe(str(audio_path))
        hypothesis = " ".join(segment.text for segment in segments)
        transcribe_seconds = time.perf_counter() - transcribe_start

        references.append(clip["reference_text"])
        hypotheses.append(hypothesis)
        total_audio_seconds += info.duration
        total_transcribe_seconds += transcribe_seconds

        # return aggregate latency + WER metrics plus a per-clip breakdown for debugging/audit.
        clip_wer = jiwer.wer(
            clip["reference_text"], hypothesis,
            reference_transform=_WER_TRANSFORM, hypothesis_transform=_WER_TRANSFORM,
        )
        per_clip.append(
            {
                "filename": clip["filename"],
                "reference": clip["reference_text"],
                "hypothesis": hypothesis,
                "wer": clip_wer,
                "transcribe_seconds": transcribe_seconds,
                "audio_seconds": info.duration,
            }
        )

    corpus_wer = jiwer.wer(
        references, hypotheses,
        reference_transform=_WER_TRANSFORM, hypothesis_transform=_WER_TRANSFORM,
    )
    rtf = total_transcribe_seconds / total_audio_seconds if total_audio_seconds > 0 else float("nan")

    return {
        "model_size": model_size,
        "device": device,
        "compute_type": compute_type,
        "load_seconds": load_seconds,
        "n_clips": len(manifest),
        "total_audio_seconds": total_audio_seconds,
        "total_transcribe_seconds": total_transcribe_seconds,
        "rtf": rtf,
        "wer": corpus_wer,
        "per_clip": per_clip,
    }