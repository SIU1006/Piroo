import json
import logging
import os

import ffmpeg
import ollama
import redis
import requests
from dotenv import load_dotenv

from settings import UPLOAD_DIR
from worker.celery_app import celery_app

load_dotenv()
assert os.getenv("LLM_MODEL") is not None, "LLM_MODEL is not set in .env"
logger = logging.getLogger(__name__)


@celery_app.task(
        name="process_video",
        autoretry_for=(requests.exceptions.RequestException,),
        retry_backoff=True, # delay autoretries for f(x) * 2s
        max_retries=3)
def process_video(task_id: str, file_path: str):

    r = redis.Redis.from_url(os.getenv("BROKER_URL", "redis://localhost:6379/0"))

    # ensure finally block sees this variable even if ffmpeg fails
    audio_path = str(UPLOAD_DIR / f"{task_id}.mp3")

    r.setex(f"heartbeat:{task_id}", 1830 + 60, f"running:{file_path}")

    try:
        # Check audio duration
        probe = ffmpeg.probe(file_path)
        duration_minutes = float(probe["format"]["duration"]) / 60

        if duration_minutes > 30:
            raise ValueError(
                f"Audio file duration exceeds 30 minutes limit: {duration_minutes:.2f} minutes"
            )

        # Extract audio
        ffmpeg.input(file_path).output(
            audio_path, vn=None, acodec="mp3", ac=1, audio_bitrate="128k"
        ).overwrite_output().run()
        logger.info(f"Audio Extracted: {task_id} at {audio_path}")

        # Send audio file to Whisper service for transcription
        whisper_url = os.getenv("WHISPER_URL", "http://localhost:3000")

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

        transcript = response.json()

        client = ollama.Client(timeout=300)
        response_llm = client.chat(
            model=os.getenv("LLM_MODEL"),
            messages=[
                {
                    "role": "user",
                    "content": f"Summarise this transcript in 3-5 sentences: {transcript}",
                }
            ],
        )
        summary = response_llm.message.content

        # Store result for late WebSocket connections (Solve pub/sub race condition)
        complete = json.dumps(
            {"status": "completed", "task_id": task_id, "summary": summary}
        )

        # use Redis as a bridge to tell browser the task is completed and send the summary back to the browser
        r.setex(f"result:{task_id}", 3600, complete)  # setex stores result for 1 hour
        r.publish(f"task:{task_id}", complete)

        logger.info(f"Task ID: {task_id}, Summary: {summary}")

    except Exception as e:
        logger.exception(f"Task {task_id} failed")
        error = json.dumps({"status": "error", "task_id": task_id, "error": str(e)})
        r.setex(f"result:{task_id}", 3600, error)
        r.publish(f"task:{task_id}", error)
        raise

    finally:
        # Clean up
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(audio_path):
            os.remove(audio_path)

@celery_app.task(name="sweep_stuck_tasks")
def sweep_stuck_tasks():
    """
    Check if there are heartbeats that are older than hard time limit but still no result stored
    >> sweep
    """
    r = redis.Redis.from_url(os.getenv("BROKER_URL", "redis://localhost:6379/0"))

    for key in r.scan_iter("heartbeat:*"):
        task_id = key.decode("utf-8").removeprefix("heartbeat:")
        if r.exists(f"result:{task_id}"): # Task ended normally
            continue

        ttl = r.ttl(key) # ttl = timetolive
        if ttl > 90:
            continue

        logger.warning(f"Task {task_id} stuck/killed, reporting error")
        error = json.dumps(
            {
                "status": "error",
                "task_id": task_id,
                "error": "Task did not finish in time and was stopped.",
            }
        )
        r.setex(f"result:{task_id}", 3600, error)
        r.publish(f"task:{task_id}", error)

        # use real path instead of guessing an extension
        heartbeat_value = r.get(key)
        original_path = None
        if heartbeat_value:
            _, _, original_path = heartbeat_value.decode("utf-8").partition(":")

        r.delete(key)

        cleanup_paths = [str(UPLOAD_DIR / f"{task_id}.mp3")]

        if original_path:
            cleanup_paths.append(original_path)
        for path in cleanup_paths:
            if os.path.exists(path):
                os.remove(path)
