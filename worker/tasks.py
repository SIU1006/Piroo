from worker.celery_app import celery_app
import ffmpeg
import os
from dotenv import load_dotenv
import redis
import json
import requests
import ollama
import logging

load_dotenv()
assert os.getenv("LLM_MODEL") is not None, "LLM_MODEL is not set in .env"
logger = logging.getLogger(__name__)

@celery_app.task(name="process_video")
def process_video(task_id: str, file_path: str):

    r = redis.Redis.from_url(os.getenv("BROKER_URL", "redis://localhost:6379/0"))    

    try:
        audio_path = f"uploads/{task_id}.mp3"

        # Extract audio only
        ffmpeg.input(file_path).output(audio_path, vn=None, acodec='mp3', ac=1, audio_bitrate="128k").overwrite_output().run()
        logger.info(f"Audio Extracted: {task_id} at {audio_path}")

        # Check audio duration
        probe = ffmpeg.probe(audio_path)
        duration_seconds = float(probe['format']['duration'])
        duration_minutes = duration_seconds / 60

        if duration_minutes > 30:
            raise ValueError(f"Audio file duration exceeds 30 minutes limit: {duration_minutes:.2f} minutes")
        
        # Send audio file to Whisper service for transcription
        whisper_url = os.getenv("WHISPER_URL", "http://localhost:3000")
        
        response = requests.post(
            f"{whisper_url}/transcribe",
            json={"audio_file": audio_path}, # Bentoml v1.4 accepts JSON by default
            timeout=700
        )

        if response.status_code != 200:
            raise ValueError(f"Whisper service error: {response.status_code}: {response.text}")
        
        transcript = response.json()

        client = ollama.Client(timeout=300)
        response_llm = client.chat(
            model=os.getenv("LLM_MODEL"),
            messages=[{"role": "user", "content": f"Summarise this transcript in 3-5 sentences: {transcript}"}]
        )
        summary = response_llm.message.content

        # Store result for late WebSocket connections (Solve pub/sub race condition)
        complete = json.dumps({
            "status": "completed",
            "task_id": task_id,
            "summary": summary
        })
            
        r.setex(f"result:{task_id}", 3600, complete) # setex stores result for 1 hour
        # use Redis as a bridge to tell browser the task is completed and send the summary back to the browser
        r.publish(f"task:{task_id}", complete)

        logger.info(f"Task ID: {task_id}, Summary: {summary}")

    except Exception as e:
        logger.exception(f"Task {task_id} failed")
        error = json.dumps({
            "status": "error",
            "task_id": task_id,
            "error": str(e)
        })
        r.setex(f"result:{task_id}", 3600, error)
        r.publish(f"task:{task_id}", error)
        raise