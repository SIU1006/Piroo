import logging
import os
import threading
import time
import uuid

import ffmpeg
import httpx
import ollama
import redis
import requests
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from redis.exceptions import RedisError

import task_store
import upload_storage
from settings import (
    BROKER_URL,
    LLM_MODEL,
    REDIS_PASSWORD,
    UPLOAD_DIR,
    WHISPER_URL,
    with_password,
)
from summary_contract import SUMMARY_OPTIONS, SUMMARY_PROMPT
from worker.celery_app import TASK_HARD_TIME_LIMIT_SECONDS, celery_app
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
STUCK_GRACE_SECONDS = 120
HEARTBEAT_TTL_SECONDS = 4 * 3600
STUCK_DEADLINE_TTL_SECONDS = (
    HEARTBEAT_TTL_SECONDS - TASK_HARD_TIME_LIMIT_SECONDS - STUCK_GRACE_SECONDS
)
ACTIVE_ATTEMPT_TTL_SECONDS = 120
ACTIVE_ATTEMPT_REFRESH_SECONDS = 30
SWEEPER_CLAIM_TTL_SECONDS = 300

START_ATTEMPT_SCRIPT = """
if ARGV[5] == '1' and not redis.call('ZSCORE', KEYS[5], ARGV[4]) then
    return 0
end
if redis.call('EXISTS', KEYS[2]) == 1 or redis.call('EXISTS', KEYS[3]) == 1
    or redis.call('EXISTS', KEYS[4]) == 1 then
    return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('SET', KEYS[2], 'running', 'EX', ARGV[3])
return 1
"""

CLAIM_STUCK_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1
    or redis.call('EXISTS', KEYS[2]) == 1
    or redis.call('EXISTS', KEYS[4]) == 1 then
    return 0
end
local heartbeat = redis.call('GET', KEYS[3])
local ttl = redis.call('TTL', KEYS[3])
if not heartbeat or ttl < 0 or ttl > tonumber(ARGV[1]) then
    return 0
end
redis.call('SET', KEYS[4], ARGV[2], 'EX', ARGV[3])
return 1
"""

RELEASE_STUCK_SCRIPT = """
if redis.call('GET', KEYS[2]) ~= ARGV[1] then
    return false
end
local heartbeat = redis.call('GET', KEYS[1])
redis.call('DEL', KEYS[1], KEYS[2])
return heartbeat
"""


class TransientServiceError(Exception):
    """A service failure that may succeed on another Celery attempt."""


RETRYABLE_EXCEPTIONS = (
    requests.exceptions.RequestException,
    httpx.TimeoutException,
    ConnectionError,
    TransientServiceError,
    RedisError,
    BotoCoreError,
    ClientError,
)


def extracted_audio_path(task_id: str) -> str:
    return str(UPLOAD_DIR / f"{task_id}.audio.mp3")


def active_attempt_key(task_id: str) -> str:
    return f"active:{task_id}"


def sweeper_claim_key(task_id: str) -> str:
    return f"sweeper-claim:{task_id}"


def keep_attempt_active(r, task_id: str) -> tuple[threading.Event, threading.Thread]:
    """Renew a lease while this worker attempt is alive, including during slow I/O."""
    stop = threading.Event()
    lease_key = active_attempt_key(task_id)

    def refresh() -> None:
        while not stop.wait(ACTIVE_ATTEMPT_REFRESH_SECONDS):
            try:
                r.setex(lease_key, ACTIVE_ATTEMPT_TTL_SECONDS, "running")
            except RedisError:
                logger.exception("Could not refresh active lease for task %s", task_id)

    thread = threading.Thread(target=refresh, name=f"task-lease-{task_id}", daemon=True)
    thread.start()
    return stop, thread

# ============= process_video() helpers =================
def start_running(task_id: str, file_path: str):
    HEARTBEAT_KEY = f"heartbeat:{task_id}"

    audio_path = extracted_audio_path(task_id)

    r = redis.Redis.from_url(with_password(BROKER_URL, _redis_password))
    acquired = bool(
        r.eval(
            START_ATTEMPT_SCRIPT,
            5,
            HEARTBEAT_KEY,
            active_attempt_key(task_id),
            sweeper_claim_key(task_id),
            f"result:{task_id}",
            task_store.OUTSTANDING_KEY,
            f"running:{file_path}",
            HEARTBEAT_TTL_SECONDS,
            ACTIVE_ATTEMPT_TTL_SECONDS,
            task_id,
            "1" if file_path.startswith("s3://") else "0",
        )
    )

    return r, audio_path, acquired

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

    if response.status_code == 429 or response.status_code >= 500:
        raise TransientServiceError(
            f"Whisper service error: {response.status_code}: {response.text}"
        )
    if response.status_code != 200:
        raise ValueError(
            f"Whisper service error: {response.status_code}: {response.text}"
        )

    return response.text

def summarize(transcript: str, model: str) -> str:
    client = ollama.Client(timeout=300)
    try:
        response_llm = client.chat(
            model=model,
            messages=[
                {"role": "user", "content": SUMMARY_PROMPT.format(transcript=transcript)},
            ],
            options=SUMMARY_OPTIONS,
        )
    except ollama.ResponseError as exc:
        if exc.status_code == 429 or exc.status_code >= 500:
            raise TransientServiceError(f"Ollama service error: {exc}") from exc
        raise
    return response_llm.message.content

def publish_result(r, task_id: str, payload: dict) -> None:
    """Store the result in Redis, notify any listening websocket"""

    task_store.finish(r, task_id, payload)

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
    r = None
    audio_path = extracted_audio_path(task_id)
    active_stop = None
    active_thread = None
    start = time.perf_counter()
    video_length_bucket = "unknown"
    will_retry = False
    attempt_acquired = False
    source_ref = file_path

    try:
        r, audio_path, attempt_acquired = start_running(task_id, file_path)
        if not attempt_acquired:
            logger.info("Task %s is active, terminal, claimed or no longer admitted", task_id)
            return
        active_stop, active_thread = keep_attempt_active(r, task_id)
        task_store.transition(r, task_id, "running", "processing",
                              TASK_HARD_TIME_LIMIT_SECONDS + STUCK_GRACE_SECONDS)
        file_path = upload_storage.materialize(source_ref, UPLOAD_DIR)
        duration_minutes = validate_duration(ffmpeg.probe(file_path))
        video_length_bucket = "under_10min" if duration_minutes < 10 else "over_10min"
        extract_audio(file_path, audio_path)
        transcript = transcribe(audio_path, WHISPER_URL)
        summary = summarize(transcript, LLM_MODEL)
        store_success(r, task_id, summary)
        metrics(task_name="process_video", status="success")

    except Exception as e:
        # Celery autoretry runs after this function exits. Keep the source and
        # withhold a terminal result while another attempt is scheduled.
        will_retry = isinstance(e, RETRYABLE_EXCEPTIONS) and self.request.retries < self.max_retries

        if will_retry:
            if r is not None:
                try:
                    task_store.transition(r, task_id, "queued", "retrying")
                except RedisError:
                    logger.exception("Could not record retry for task %s", task_id)
            metrics(
                task_name="process_video",
                status="retry",
                exception_type=type(e).__name__,
                video_length_bucket=video_length_bucket,
            )
        else:
            if r is not None:
                try:
                    store_failure(r, task_id, e)
                except RedisError as publish_error:
                    # This publication failure is retryable. Set the flag before
                    # re-raising so finally preserves the source for Celery.
                    will_retry = True
                    metrics(
                        task_name="process_video",
                        status="retry",
                        exception_type=type(publish_error).__name__,
                        video_length_bucket=video_length_bucket,
                    )
                    raise
            else:
                logger.error("Task %s failed before connecting to Redis: %s", task_id, e)
            metrics(
                task_name="process_video",
                status="failure",
                exception_type=type(e).__name__,
                video_length_bucket=video_length_bucket,
        )
        raise

    finally:
        if active_stop is not None:
            active_stop.set()
            active_thread.join()
            try:
                r.delete(active_attempt_key(task_id))
            except RedisError:
                logger.exception("Could not release active lease for task %s", task_id)
        if r is not None and attempt_acquired and not will_retry:
            try:
                r.delete(f"heartbeat:{task_id}")
            except RedisError:
                logger.exception("Could not remove heartbeat for task %s", task_id)
        metrics(task_name="process_video", start=start, video_length_bucket=video_length_bucket)

        if not attempt_acquired:
            # The sweeper that owns the claim is responsible for these files.
            pass
        elif will_retry:
            # keep file_path
            cleanup(audio_path)
            if source_ref.startswith("s3://"):
                cleanup(file_path)
        else:
            cleanup(file_path, audio_path)
            if source_ref.startswith("s3://"):
                try:
                    upload_storage.delete(source_ref)
                except (BotoCoreError, ClientError):
                    logger.exception("Could not delete upload %s; bucket lifecycle will expire it", task_id)


# ============= sweep_stuck_tasks() helpers =============
def find_stuck(r):
    """Atomically claim unfinished attempts past the hard limit and grace period."""

    for key in r.scan_iter("heartbeat:*"):
        heartbeat_key = key.decode("utf-8") if isinstance(key, bytes) else key
        task_id = heartbeat_key.removeprefix("heartbeat:")
        claim_token = uuid.uuid4().hex
        claimed = r.eval(
            CLAIM_STUCK_SCRIPT,
            4,
            f"result:{task_id}",
            active_attempt_key(task_id),
            heartbeat_key,
            sweeper_claim_key(task_id),
            STUCK_DEADLINE_TTL_SECONDS,
            claim_token,
            SWEEPER_CLAIM_TTL_SECONDS,
        )
        if claimed:
            yield task_id, heartbeat_key, claim_token

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

def cleanup_stuck(r, task_id: str, heartbeat_key, claim_token: str) -> None:
    """Release an owned claim and remove the stuck task's leftover files."""
    heartbeat_value = r.eval(
        RELEASE_STUCK_SCRIPT,
        2,
        heartbeat_key,
        sweeper_claim_key(task_id),
        claim_token,
    )
    if not heartbeat_value:
        return

    if isinstance(heartbeat_value, bytes):
        heartbeat_value = heartbeat_value.decode("utf-8")
    _, _, original_path = heartbeat_value.partition(":")

    cleanup_paths = [extracted_audio_path(task_id)]

    if original_path:
        if original_path.startswith("s3://"):
            cleanup_paths.append(str(UPLOAD_DIR / original_path.rsplit("/", 1)[-1]))
        else:
            cleanup_paths.append(original_path)
    cleanup(*cleanup_paths)
    if original_path and original_path.startswith("s3://"):
        try:
            upload_storage.delete(original_path)
        except (BotoCoreError, ClientError):
            logger.exception("Could not remove stuck upload %s", task_id)
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
        for task_id, ref in task_store.expire_overdue(r):
            try:
                if ref.startswith("s3://"):
                    cleanup(str(UPLOAD_DIR / ref.rsplit("/", 1)[-1]), extracted_audio_path(task_id))
                upload_storage.delete(ref)
            except (BotoCoreError, ClientError, OSError):
                logger.exception("Could not remove expired upload %s", task_id)
        for task_id, heartbeat_key, claim_token in find_stuck(r):
            report_stuck(r, task_id)
            cleanup_stuck(r, task_id, heartbeat_key, claim_token)
        TASK_TOTAL.labels(task_name="sweep_stuck_tasks", status="success").inc()

    except Exception as e:
        logger.error(f"Error sweeping stuck tasks: {e}")
        metrics(task_name="sweep_stuck_tasks", status="failure", exception_type=type(e).__name__)
        raise

    finally:
        metrics(task_name="sweep_stuck_tasks", start=start)

# registers check_canary_wer with celery_app as a side effect of this import
from worker import canary  # noqa: F401





