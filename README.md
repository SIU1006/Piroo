# video2text-pipeline

This is a video-to-text transcription pipeline built with FastAPI, Celery and Redis.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green)
![Celery](https://img.shields.io/badge/Celery-5.x-orange)
![Docker](https://img.shields.io/badge/Docker-Compose-blue)
![License](https://img.shields.io/badge/License-MIT-green)

---

## The Problem This Solves

Processing a 50MB video file over a standard HTTP request causes the browser to timeout before the server finishes. The solution is to decouple receiving the job from doing the job — FastAPI accepts the upload and returns a `task_id` in under 200ms, while a Celery worker handles the heavy processing independently in the background.

---

## Architecture

```
Browser
  │
  │  POST /api/v1/upload (video file)
  ▼
FastAPI (Producer)
  │  returns {"task_id": "...", "status": "queued"} instantly
  │
  │  enqueues task
  ▼
Redis (Message Broker)
  │
  │  dequeues task
  ▼
Celery Worker (Consumer)
  │
  ├── ffmpeg → extracts audio (.mp3)
  ├── faster-whisper → transcribes audio to text
  ├── ollama / llama3.2 → summarises transcript
  │
  │  publishes result to Redis pub/sub channel
  ▼
FastAPI (WebSocket)
  │
  │  pushes result down open WebSocket connection
  ▼
Browser receives summary in real time
```

FastAPI and the Celery worker are **completely separate processes** communicating exclusively through Redis. A 2-minute video processing job has zero impact on API response time.

---

## Tech Stack

| Technology | Why |
|---|---|
| **FastAPI** | Async-native Python web framework. Returns `task_id` in <200ms without blocking on video processing. |
| **Celery** | Distributed task queue. Runs the heavy processing (ffmpeg, Whisper, LLM) in a separate worker process entirely decoupled from the API. |
| **Redis** | Acts as both the Celery message broker (task queue) and pub/sub bus (notifying FastAPI when a task completes). |
| **ffmpeg** | Extracts the audio stream from uploaded video files. Converts to mono MP3 at 128kbps — reduces file size significantly before sending to Whisper. |
| **faster-whisper** | Open-source reimplementation of OpenAI Whisper using CTranslate2. Runs on CPU, no GPU required, no API costs. Model loaded once at worker startup, not per task. |
| **ollama / llama3.2** | Local LLM inference. Summarises the raw transcript into structured feedback. No external API dependency, no usage costs. |
| **WebSocket** | Pushes the completed result to the browser the moment processing finishes. Eliminates polling — 500 concurrent users means 500 silent connections, not 500 requests/second. |
| **Docker Compose** | Single-command startup for all services: FastAPI, Celery worker, Redis, and ollama. Healthchecks ensure Redis is ready before workers connect. |

---

## How to Run

### Prerequisites
- Docker Desktop installed and running
- Git

### Start the pipeline

```bash
git clone https://github.com/your-username/video2text-pipeline.git
cd video2text-pipeline
docker compose up
```

On first run, Docker will build the images and pull the ollama container. Once all services are running, pull the LLM model:

```bash
docker exec -it asyncvtp-ollama-1 ollama pull llama3.2
```

### Test it

1. Open `http://localhost:8000/docs`
2. Upload a video file via `POST /api/v1/upload`
3. Copy the `task_id` from the response
4. Open `test_ws.html` in your browser
5. Paste the `task_id` and click **Connect WebSocket**
6. Wait — the summary will appear automatically when processing completes

---

## API Reference

### `POST /api/v1/upload`

Accepts a video file upload. Saves the file, enqueues a Celery task, and returns immediately.

**Request:** `multipart/form-data` with a `file` field

**Response:**
```json
{
  "task_id": "a3f9c2d1-4b5e-...",
  "status": "queued",
  "filename": "meeting.mp4"
}
```

---

### `WS /api/v1/ws/{task_id}`

WebSocket endpoint. Connect after uploading — the server pushes one message when processing completes, then closes the connection.

**Push message:**
```json
{
  "status": "completed",
  "task_id": "a3f9c2d1-4b5e-...",
  "summary": "The recording discusses..."
}
```

---

## Project Structure

```
video2text-pipeline/
│
├── app/
│   ├── main.py              # FastAPI app, router registration
│   ├── routes/
│   │   ├── upload.py        # POST /upload endpoint
│   │   └── websocket.py     # WebSocket endpoint + Redis pub/sub listener
│   └── schemas/
│       └── task.py          # Pydantic response models
│
├── worker/
│   ├── celery_app.py        # Celery instance, broker config
│   └── tasks.py             # process_video task: ffmpeg → Whisper → LLM → publish
│
├── Dockerfile               # Python 3.11 + ffmpeg
├── docker-compose.yml       # All services: FastAPI, Celery, Redis, ollama
├── requirements.txt
└── .env                     # BROKER_URL, BACKEND_URL (not committed)
```

---

## Key Engineering Decisions

**Why Celery instead of Python `asyncio` for background processing?**

`asyncio` is non-blocking within a single process — it handles many requests concurrently but can't escape the GIL for CPU-bound work. Celery runs tasks in a completely separate process, meaning a 2-minute ffmpeg + Whisper job has literally zero impact on the FastAPI server's ability to handle new requests.

**Why Redis for both broker and pub/sub?**

Redis serves two roles: message broker (Celery task queue) and pub/sub bus (notifying FastAPI when a task completes). Using one service for both reduces infrastructure complexity. The Celery worker publishes to a Redis channel keyed by `task_id`; FastAPI subscribes to that channel and forwards the message down the open WebSocket connection.

**Why faster-whisper instead of the OpenAI Whisper API?**

faster-whisper runs locally with no API costs or rate limits. The model is loaded once at worker startup (not per task), so subsequent tasks reuse the same weights already in memory — a standard MLOps pattern for inference servers.

---

## What I Learned

- **Async architecture design** — understanding the difference between non-blocking I/O (`asyncio`) and true process-level decoupling (Celery). These solve different problems and are often used together.
- **Redis as infrastructure glue** — using Redis for both task queuing and pub/sub messaging to bridge two separate processes (Celery worker → FastAPI → browser) without shared memory.
- **Production-grade patterns** — model loading at startup, path traversal security in file uploads, healthcheck-gated service startup in Docker Compose, and environment variable management across containerised services.
