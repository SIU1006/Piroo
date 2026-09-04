import json
import logging
import time
from pathlib import Path

import jiwer
import requests

from settings import WHISPER_URL
from worker.celery_app import celery_app
from worker.metrics import CANARY_WER, TASK_DURATION_SECONDS, TASK_FAILURES_TOTAL, TASK_TOTAL

'''
Live whisper-service checker. Runs periodically on celery-beat's schedule to transcribe a set of known reference clips and record the WER against the known reference text.
Run python worker/canary_clips/select_canary_clips.py first to get the clips into the image.
'''

logger = logging.getLogger(__name__)

CANARY_DIR = Path(__file__).parent / "canary_clips"
CANARY_MANIFEST = CANARY_DIR / "manifest.json"

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
    try:
        manifest = _load_canary_manifest()
        references, hypotheses = [], []

        for clip in manifest:
            audio_path = CANARY_DIR / clip["filename"]
            with open(audio_path, "rb") as f:
                response = requests.post(
                    f"{WHISPER_URL}/transcribe",
                    files={"audio_file": (clip["filename"], f, "audio/wav")},
                    timeout=120,
                )
            response.raise_for_status()
            hypothesis = response.json()
            references.append(clip["reference_text"])
            hypotheses.append(hypothesis)

        wer = jiwer.wer(
            references, hypotheses,
            reference_transform=_WER_TRANSFORM, hypothesis_transform=_WER_TRANSFORM,
        )
        CANARY_WER.set(wer)
        logger.info(f"Canary WER check: {wer:.3f} across {len(manifest)} clips")

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