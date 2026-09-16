import json
import logging
import os
import time
from pathlib import Path

import ffmpeg
import jiwer
import redis
import requests

from model_eval.provenance import dataset_identity, load_config, revision_for
from worker.celery_app import celery_app
from worker.metrics import (
    CANARY_LAST_SUCCESS_UNIX_SECONDS,
    CANARY_WER,
    TASK_DURATION_SECONDS,
    TASK_FAILURES_TOTAL,
    TASK_TOTAL,
)

'''
Live whisper-service checker. Runs periodically on celery-beat's schedule to transcribe a set of known reference clips and record the WER against the known reference text.
Run python worker/canary_clips/select_canary_clips.py first to get the clips into the image.
'''

logger = logging.getLogger(__name__)

CANARY_DIR = Path(__file__).parent / "canary_clips"
CANARY_MANIFEST = CANARY_DIR / "manifest.json"
CANARY_WHISPER_URL = os.getenv("CANARY_WHISPER_URL", "")
CANARY_WHISPER_IMAGE = os.getenv("CANARY_WHISPER_IMAGE", "")

# Same as model_eval/benchmark.py
_WER_TRANSFORM = jiwer.Compose(
    [
        jiwer.ToLowerCase(),
        jiwer.RemovePunctuation(),
        jiwer.RemoveMultipleSpaces(),
        jiwer.Strip(),
        jiwer.ReduceToListOfListOfWords(),
    ]
)


def _load_canary_manifest() -> list[dict]:
    if not CANARY_MANIFEST.exists():
        raise FileNotFoundError(
            f"{CANARY_MANIFEST} not found. Run "
            "`python worker/canary_clips/select_canary_clips.py` first, then commit the result."
        )
    return json.loads(CANARY_MANIFEST.read_text())


@celery_app.task(name="check_canary_wer")
def check_canary_wer():
    """Runs on celery-beat's schedule (see worker/celery_app.py). Transcribes
    each canary clip through the live whisper-service and records WER
    against its known reference text.
    """
    start = time.perf_counter()
    if not CANARY_WHISPER_URL:
        logger.info("Candidate canary disabled; no candidate-only endpoint configured")
        return None
    try:
        if "@sha256:" not in CANARY_WHISPER_IMAGE:
            raise ValueError("Candidate canary requires its deployed image digest")
        metadata = requests.post(f"{CANARY_WHISPER_URL}/metadata", timeout=30).json()
        config = load_config()
        if metadata["model_revision"] != revision_for(metadata["model_size"]):
            raise ValueError("Unexpected candidate model revision")
        if metadata["transcribe"] != config["transcribe"]:
            raise ValueError("Candidate decoding configuration differs from evaluation")
        manifest = _load_canary_manifest()
        references, hypotheses = [], []

        for clip in manifest:
            audio_path = CANARY_DIR / clip["filename"]
            with open(audio_path, "rb") as f:
                response = requests.post(
                    f"{CANARY_WHISPER_URL}/transcribe",
                    files={"audio_file": (clip["filename"], f, "audio/wav")},
                    timeout=120,
                )
            response.raise_for_status()
            hypothesis = response.text
            references.append(clip["reference_text"])
            hypotheses.append(hypothesis)

        wer = jiwer.wer(
            references, hypotheses,
            reference_transform=_WER_TRANSFORM, hypothesis_transform=_WER_TRANSFORM,
        )
        config["filenames"] = [clip["filename"] for clip in manifest]
        identity, _ = dataset_identity(CANARY_DIR, config)
        report = {"wer": wer, "evaluation": identity, "model": metadata,
                  "deployed_image": CANARY_WHISPER_IMAGE, "endpoint": CANARY_WHISPER_URL,
                  "evaluated_at": time.time()}
        with redis.Redis.from_url(celery_app.conf.broker_url) as client:
            client.setex("canary:evaluation", 3600, json.dumps(report))
        CANARY_WER.set(wer)
        logger.info("Candidate canary WER %.3f across %s clips; revision=%s endpoint=%s",
                    wer, len(manifest), metadata["model_revision"], CANARY_WHISPER_URL)

        TASK_TOTAL.labels(task_name="check_canary_wer", status="success").inc()
        return wer

    except Exception as e:
        TASK_TOTAL.labels(task_name="check_canary_wer", status="failure").inc()
        TASK_FAILURES_TOTAL.labels(task_name="check_canary_wer", exception_type=type(e).__name__).inc()
        raise

    finally:
        TASK_DURATION_SECONDS.labels(task_name="check_canary_wer", video_length_bucket="n/a").observe(
            time.perf_counter() - start
        )
