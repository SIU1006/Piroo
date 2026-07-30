from worker.celery_app import celery_app
import ffmpeg
import os
from dotenv import load_dotenv
import redis
import json
import requests
import ollama

load_dotenv()

@celery_app.task(name="process_video")
def process_video(task_id: str, file_path: str):
    audio_path = f"uploads/{task_id}.mp3"

    # Extract audio only
    ffmpeg.input(file_path).output(audio_path, vn=None, acodec='mp3', ac=1, audio_bitrate="128k").overwrite_output().run()
    print(f"Audio Extracted: {task_id} at {audio_path}")

    # Check file size
    size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    if size_mb > 25:
        raise ValueError(f"Audio file size exceeds 25MB limit: {size_mb:.2f}MB")

    # Send audio file to Whisper service for transcription
    whisper_url = os.getenv("WHISPER_URL", "http://localhost:3000")
    
    response = requests.post(
        f"{whisper_url}/transcribe",
        json={"audio_file": audio_path} # Bentoml v1.4 accepts JSON by default
    )

    if response.status_code != 200:
        raise ValueError(f"Whisper service errorL {response.status_code}: {response.text}")
    
    transcript = response.text

    response_llm = ollama.chat(
        model="llama3.2",
        messages=[{"role": "user", "content": f"Summarise this transcript in 3-5 sentences: {transcript}"}]
    )
    summary = response_llm.message.content

    print(f"Task ID: {task_id}, Summary: {summary}")
    # use Redis as a bridge to tell browser the task is completed and send the summary back to the browser
    r = redis.Redis.from_url(os.getenv("BROKER_URL", "redis://localhost:6379/0"))    
    r.publish(f"task:{task_id}", json.dumps({    
        "status": "completed",
        "task_id": task_id,
        "summary": summary
    }))

