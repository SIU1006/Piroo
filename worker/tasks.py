import json
import logging
import os
import time

import ffmpeg
import ollama
import redis
import requests
from dotenv import load_dotenv

from settings import (
    BROKER_URL,
    LLM_MODEL,
    REDIS_PASSWORD,
    UPLOAD_DIR,
    WHISPER_URL,
    with_password,
)
from worker.celery_app import celery_app
from worker.metrics import (
    CANARY_WER,  # noqa: F401 - re-exported so worker/canary.py can share one metrics module
    TASK_DURATION_SECONDS,
    TASK_FAILURES_TOTAL,
    TASK_TOTAL,
    ensure_metrics_server_started,
)

load_dotenv()
assert os.getenv("LLM_MODEL") is not None, "LLM_MODEL is not set in .env"
_redis_password = REDIS_PASSWORD or os.getenv("REDIS_PASSWORD")
logger = logging.getLogger(__name__)
ensure_metrics_server_started()

RESULT_TTL_SECONDS = 3600
RETRYABLE_EXCEPTIONS = (requests.exceptions.RequestException,)

# ============= process_video() helpers =================
def start_running(task_id: str, file_path: str):
    HEARTBEAT_TTL_SECONDS = 1830 + 60  # hard time limit + 60s grace
    HEARTBEAT_KEY = f"heartbeat:{task_id}"

    audio_path = str(UPLOAD_DIR / f"{task_id}.mp3")

    r = redis.Redis.from_url(with_password(BROKER_URL, _redis_password))
    r.setex(
        HEARTBEAT_KEY,
        HEARTBEAT_TTL_SECONDS,
        f"running:{file_path}")

    return r, audio_path

def validate_duration(probe_result: dict, max_minutes: int = 30):
    # Check audio duration
    duration = float(probe_result["format"]["duration"]) / 60
    if duration > max_minutes:
        raise ValueError(
            f"Audio file duration exceeds {max_minutes} minutes limit: {duration:.2f} minutes"
            )
    return duration

def extract_audio(file_path: str, audio_path: str) -> None:
    # Extract audio
    ffmpeg.input(file_path).output(
        audio_path, vn=None, acodec="mp3", ac=1, audio_bitrate="128k"
    ).overwrite_output().run()
    logger.info(f"Audio Extracted: {audio_path}")

def transcribe(audio_path: str, whisper_url: str) -> str:
    # Send audio file to Whisper service for transcription

    with open(audio_path, "rb") as f:
        response = requests.post(
            f"{whisper_url}/transcribe",
            files = {"audio_file": (os.path.basename(audio_path), f, "audio/mpeg")},
            timeout=700,
        )

    if response.status_code != 200:
        raise ValueError(
            f"Whisper service error: {response.status_code}: {response.text}"
        )

    return response.text

def summarize(transcript: str, model: str) -> str:
    client = ollama.Client(timeout=300)
    response_llm = client.chat(
        model=model,
        messages=[
            {
                "role": "user",
                "content": f"Summarise this transcript in 3-5 sentences: {transcript}",
            }
        ],
    )
    return response_llm.message.content

def publish_result(r, task_id: str, payload: dict) -> None:
    """Store the result in Redis, notify any listening websocket"""

    RESULT_KEY = f"result:{task_id}"
    TASK_CHANNEL = f"task:{task_id}"

    message = json.dumps(payload)
    r.setex(RESULT_KEY, RESULT_TTL_SECONDS, message)
    r.publish(TASK_CHANNEL, message)

def store_success(r, task_id, summary):
    message = {
        "status": "completed",
        "task_id": task_id,
        "summary": summary
    }
    publish_result(r, task_id, message)
    logger.info(f"Task ID: {task_id}, Summary: {summary}")

def store_failure(r, task_id, e):
    message = {
        "status": "error",
        "task_id": task_id,
        "error": str(e)
    }
    publish_result(r, task_id, message)
    logger.error(f"Task ID: {task_id}, error: {e}")

def cleanup(*paths: str) -> None:
    for path in paths:
        if os.path.exists(path):
            os.remove(path)

def metrics(
    task_name: str,
    status: str | None = None,
    exception_type: str | None = None,
    start: float | None = None,
    video_length_bucket: str | None = None,
):
    '''Perform Prometheus metrics'''
    if status:
        TASK_TOTAL.labels(task_name=task_name, status=status).inc()
    if exception_type:
        TASK_FAILURES_TOTAL.labels(task_name=task_name, exception_type=exception_type).inc()
    if start is not None:
        TASK_DURATION_SECONDS.labels(
            task_name=task_name,
            video_length_bucket=video_length_bucket or "unknown",
        ).observe(time.perf_counter() - start)
# =======================================================


@celery_app.task(
        bind=True, # pass self to task as first arg
        name="process_video",
        autoretry_for=RETRYABLE_EXCEPTIONS,
        retry_backoff=True, # delay autoretries for f(x) * 2s
        max_retries=3)
def process_video(self, task_id: str, file_path: str):
    r, audio_path = start_running(task_id, file_path)
    start = time.perf_counter()
    video_length_bucket = "unknown"
    will_retry = False

    try:
        duration_minutes = validate_duration(ffmpeg.probe(file_path))
        video_length_bucket = "under_10min" if duration_minutes < 10 else "over_10min"
        extract_audio(file_path, audio_path)
        transcript = transcribe(audio_path, WHISPER_URL)
        summary = summarize(transcript, LLM_MODEL)
        store_success(r, task_id, summary)

    except Exception as e:
        '''
        Celery autoretry only works after process_video() is fully completed.
        So we need to predict if the task will retry, then dont tell the client it failed yet,
        otherwise cleanup() will delete the input file so client cant retry
        '''
        will_retry = isinstance(e, RETRYABLE_EXCEPTIONS) and self.request.retries < self.max_retries

        if will_retry:
            metrics(
                task_name="process_video",
                status="retry",
                exception_type=type(e).__name__,
                video_length_bucket=video_length_bucket,
            )
        else:
            store_failure(r, task_id, e)
            metrics(
                task_name="process_video",
                status="failure",
                exception_type=type(e).__name__,
                video_length_bucket=video_length_bucket,
        )
        raise

    finally:
        metrics(task_name="process_video", start=start, video_length_bucket=video_length_bucket)

        if will_retry:
            # keep file_path
            cleanup(audio_path)
        else:
            cleanup(file_path, audio_path)


# ============= sweep_stuck_tasks() helpers =============
def find_stuck(r):
    '''yield taskid & key for heartbeats with no stored result yet lived too long'''

    STUCK_TTL_THRESHOLD_SECONDS = 90  # sweep treats <90s left on the heartbeat as stuck

    for key in r.scan_iter("heartbeat:*"):
        task_id = key.decode("utf-8").removeprefix("heartbeat:")
        if r.exists(f"result:{task_id}"): # Task ended normally
            continue

        ttl = r.ttl(key)
        if ttl > STUCK_TTL_THRESHOLD_SECONDS:
            continue

        yield task_id, key

def report_stuck(r, task_id: str) -> None:
    logger.warning(f"Task {task_id} stuck/killed, reporting error")
    message = {
            "status": "error",
            "task_id": task_id,
            "error": "Task did not finish in time and was stopped.",
        }
    publish_result(r, task_id, message)

def get_originalpath(r, heartbeat_key) -> str | None:
    '''Get source file path stored in heartbeat's value instead of guessing an extension.'''
    heartbeat_value = r.get(heartbeat_key)
    if not heartbeat_value:
        return None

    _, _, original_path = heartbeat_value.decode("utf-8").partition(":")
    return original_path or None

def cleanup_stuck(r, task_id: str, heartbeat_key) -> None:
    """Remove heartbeat key and leftover files from the stuck task"""
    original_path = get_originalpath(r, heartbeat_key)
    r.delete(heartbeat_key)

    cleanup_paths = [str(UPLOAD_DIR / f"{task_id}.mp3")]

    if original_path:
        cleanup_paths.append(original_path)
    cleanup(*cleanup_paths)
# =======================================================

@celery_app.task(name="sweep_stuck_tasks")
def sweep_stuck_tasks():
    """
    Check if there are heartbeats that are older than hard time limit but still no result stored
    >> sweep
    """
    r = redis.Redis.from_url(with_password(BROKER_URL, _redis_password))
    start = time.perf_counter()

    try:
        for task_id, heartbeat_key in find_stuck(r):
            report_stuck(r, task_id)
            cleanup_stuck(r, task_id, heartbeat_key)
        TASK_TOTAL.labels(task_name="sweep_stuck_tasks", status="success").inc()

    except Exception as e:
        logger.error(f"Error sweeping stuck tasks: {e}")
        metrics(task_name="sweep_stuck_tasks", status="failure", exception_type=type(e).__name__)
        raise

    finally:
        metrics(task_name="sweep_stuck_tasks", start=start)

# registers check_canary_wer with celery_app as a side effect of this import
from worker import canary  # noqa: F401





