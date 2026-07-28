from worker.celery_app import celery_app
import ffmpeg
import os
from dotenv import load_dotenv
from faster_whisper import WhisperModel
import ollama
import redis
import json

load_dotenv()
model = WhisperModel("base", device="cpu", compute_type="int8")


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

    # faster-whisper transcription
    segments, info = model.transcribe(audio_path, beam_size=5)
    segments = list(segments)

    print("Detected language '%s' with probability %f" % (info.language, info.language_probability))

    # LLM Summarization (ollama)
    transcript_text = " ".join([segment.text for segment in segments])
    prompt = f"You are a helpful assistant. Below is a transcript from an audio recording. Write a concise summary in 3-5 sentences covering the main topics discussed. Transcript: {transcript_text}"

    response = ollama.chat(
        model="llama3.2", 
        messages=[{"role": "user", "content": prompt}]
        )

    summary = response.message.content
    print(f"Task ID: {task_id}, Summary: {summary}")
    # use Redis as a bridge to tell browser the task is completed and send the summary back to the browser
    r = redis.Redis.from_url(os.getenv("BROKER_URL", "redis://localhost:6379/0"))    
    r.publish(f"task:{task_id}", json.dumps({    
        "status": "completed",
        "task_id": task_id,
        "summary": summary
    }))

