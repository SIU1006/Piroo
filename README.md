# AsyncVTP — Async Video-to-Text Pipeline

A ML inference pipeline: upload a video, receive an AI-generated summary in real time. Built incrementally from a single FastAPI endpoint into a fully observable, autoscaling Kubernetes deployment with self-hosted inference, canary releases, and CI/CD.

---

## Note
This project is still in beta. I am trying to polish it and write more tests so that it can be fully functionable and bug-free. I will remove this section when it is ready to be used.

This is my first MLOPS related project. I am trying my best to work it through and learn as much as possible. A star would be huge motivation to me!

---

## Why This Architecture

Transcribing a 50 MB video inside a standard HTTP request guarantees browser timeouts and blocked server threads. The solution is to **decouple job ingestion from job execution**:

- **FastAPI** acknowledges uploads in under 200 ms with a `task_id`.
- A **Celery** worker fleet performs the heavy pipeline — ffmpeg audio extraction, Whisper transcription, LLM summarisation — in completely separate processes.
- The result is pushed to the browser over **WebSocket** via Redis pub/sub. No polling, no dangling HTTP connections.

A 2-minute transcription job has zero impact on API response time.

---

## Architecture

```
Browser
  │
  │  POST /api/v1/upload (video file)
  ▼
FastAPI (Producer) ─────── returns {"task_id", "status": "queued"} instantly
  │                           serves web UI · exposes /metrics for Prometheus
  │  enqueues task
  ▼
Redis ── Celery broker · pub/sub bus · result cache
  │
  │  dequeues task
  ▼
Celery Worker (Consumer) ── autoscaled 1–5 pods via HPA
  │
  ├── 1. ffmpeg              → extract mono 128 kbps MP3 (30 min limit)
  ├── 2. BentoML Whisper     → POST /transcribe  (faster-whisper, CPU / int8)
  ├── 3. Ollama llama3.2     → summarise transcript
  └── 4. Redis               → publish result + cache it with 1 h TTL
  │
  │  publishes to channel task:{task_id}
  ▼
FastAPI WebSocket /api/v1/ws/{task_id}
  │
  │  pushes result down the open connection
  ▼
Browser receives the summary in real time

Observability (sidecar): Prometheus scrapes FastAPI /metrics → Grafana dashboards
```

FastAPI and the Celery workers are **separate processes communicating exclusively through Redis**. The Whisper model is served by a **standalone BentoML inference service**, so it can be versioned, scaled, and canary-released independently of the workers that call it.

---

## Features

- **Asynchronous job processing** — Celery + Redis task queue fully decoupled from the API
- **Self-hosted speech-to-text** — faster-whisper behind a BentoML inference service (CPU, int8 quantised, no GPU or API costs)
- **Local LLM summarisation** — Ollama running llama3.2, no external API dependency
- **Real-time delivery** — WebSocket push with a Redis-backed result cache that survives late connections
- **Kubernetes-native** — Deployments, Services, PVCs, ConfigMaps, and a CPU-based HorizontalPodAutoscaler
- **Canary inference releases (opt-in)** — stable and canary Whisper pods can be served behind one Service via label selectors; not applied by default, see [Model Management & Canary Releases](#model-management--canary-releases)
- **Full observability** — Prometheus metrics, prebuilt Grafana dashboard and Locust for load testing
- **MLOps lifecycle** — MLflow experiment tracking and model registry; GitHub Actions CI that tests, builds, and pushes images to GHCR

---

## Tech Stack

| Component | Technology | Role |
|---|---|---|
| API layer | **FastAPI** | Async upload endpoint, WebSocket gateway, static UI, `/metrics` |
| Task queue | **Celery** | Runs ffmpeg → Whisper → LLM pipeline in isolated worker processes |
| Broker / bus | **Redis** | Triple duty: Celery broker, pub/sub channel, result cache (1 h TTL) |
| Media processing | **ffmpeg** | Extracts mono 128 kbps MP3 from video; duration probing |
| Speech-to-text | **faster-whisper** via **BentoML** | Dedicated inference service; model loaded once at startup |
| Summarisation | **Ollama / llama3.2** | Local LLM inference, zero usage cost |
| Orchestration | **Kubernetes (kind)** | All services as manifests; HPA scales Celery on 70 % CPU |
| Monitoring | **Prometheus + Grafana** | API metrics, dashboards, Celery introspection |
| Load testing | **Locust** | Simulated concurrent uploads against the cluster |
| Experiment tracking | **MLflow** | Logs Whisper model candidates (params, RTF, WER against a labeled eval set), registers + promotes via aliases |
| CI/CD | **GitHub Actions** | pytest gate → Docker builds → push to GHCR |

---

## Project Roadmap

| Phase | Milestone | Status |
|---|---|---|
| 1 | FastAPI upload endpoint | Complete |
| 2 | Celery + Redis async queue | Complete |
| 3 | ffmpeg audio extraction | Complete |
| 4 | LLM summarisation + WebSocket push | Complete |
| 5 | Docker Compose multi-service stack | Complete |
| 6 | Self-hosted inference service with BentoML | Complete |
| 7 | Kubernetes deployment + HPA autoscaling | Complete |
| 8 | Prometheus + Grafana + Locust load testing | Complete |
| 9 | MLflow + GitHub Actions CI/CD + canary deployment | Complete |

---

## Getting Started

### Prerequisites

- Docker Desktop (with Kubernetes CLI `kubectl` and [kind](https://kind.sigs.k8s.io/) for the cluster path)
- Git

### Option A — Docker Compose (local development)

```bash
git clone https://github.com/borissiu1006/video2text-pipeline.git
cd video2text-pipeline
docker compose up --build
```

Once the stack is healthy, pull the LLM into the Ollama container (first run only):

```bash
docker compose exec ollama ollama pull llama3.2
```

| Service | URL |
|---|---|
| Web UI / API | http://localhost:8000 (`/docs` for Swagger) |
| Whisper inference service | http://localhost:3000 |
| Ollama | http://localhost:11434 |

### Option B — Kubernetes (kind cluster)

Create the cluster with host port mappings preconfigured:

```bash
kind create cluster --config kind-config.yml
```

Build the images and load them into kind (`imagePullPolicy: Never` is set in the manifests):

```bash
Install the chart:
helm install asyncvtp k8s -f k8s/values.yaml -f k8s/values-dev.yaml

Wait for ollama to be ready, then pull the model:
kubectl wait --for=condition=available --timeout=120s deployment/ollama
kubectl exec -it deploy/ollama -- ollama pull llama3.2

To preview the rendered manifests without installing:
helm template asyncvtp k8s -f k8s/values.yaml -f k8s/values-dev.yaml

To validate the chart:
helm lint k8s

Upgrade after changing values:
helm upgrade asyncvtp k8s -f k8s/values.yaml -f k8s/values-dev.yaml

Uninstall:
helm uninstall asyncvtp
```

> The Whisper model is baked into the image at build time via the `WHISPER_MODEL_SIZE` build arg (default `base`), so it doesn't need to be downloaded again on every pod restart. If you also want to build the canary's `small`-model image, see [Model Management & Canary Releases](#model-management--canary-releases).


> **Note:** `k8s/whisper-canary.yml` is intentionally excluded from the default deploy — see [Model Management & Canary Releases](#model-management--canary-releases) below if you want to apply it.

Before deploying, create the required Secrets (they are **not** committed — see [Secrets](#secrets)):

```bash
kubectl create secret generic redis-secret \
  --from-file=redis-password=secrets/redis-password
kubectl create secret generic grafana-secret \
  --from-file=admin-password=secrets/grafana-admin-password
kubectl create secret generic alertmanager-secret \
  --from-file=slack-webhook-url=secrets/slack-webhook-url
```

| Service | URL |
|---|---|
| FastAPI (NodePort 30000) | http://localhost:8080 |
| Prometheus (NodePort 30090) | http://localhost:9090 |
| Grafana (NodePort 30030) | http://localhost:3001 (admin / admin) |
| MLflow (NodePort 30050) | http://localhost:3002 |

> **Note:** the Celery HPA requires [metrics-server](https://github.com/kubernetes-sigs/metrics-server) in the cluster (not shipped with kind by default). Verify with `kubectl top pods`, then watch autoscaling under load via `kubectl get hpa -w`.

---

## Secrets

Kubernetes Secrets in this project are **not committed to git**. Instead, `secrets/` ships example templates — copy one to a gitignored file, fill in the real value, and create the Secret imperatively:

```bash
cp secrets/slack-webhook-url.example secrets/slack-webhook-url
# edit secrets/slack-webhook-url with your real Slack Incoming Webhook URL
kubectl create secret generic alertmanager-secret \
  --from-file=slack-webhook-url=secrets/slack-webhook-url

cp secrets/redis-password.example secrets/redis-password
kubectl create secret generic redis-secret \
  --from-file=redis-password=secrets/redis-password

cp secrets/grafana-admin-password.example secrets/grafana-admin-password
kubectl create secret generic grafana-secret \
  --from-file=admin-password=secrets/grafana-admin-password
```

| Secret | Key | Used by |
|---|---|---|
| `alertmanager-secret` | `slack-webhook-url` | Alertmanager Slack receiver |
| `redis-secret` | `redis-password` | Redis `requirepass`, consumers, and the exporter |
| `grafana-secret` | `admin-password` | Grafana admin login |

All files under `secrets/` except `*.example` are ignored by git, so a real webhook URL or password can never be committed by accident. The Redis password must be kept identical between `redis-secret` and the `REDIS_PASSWORD` used by FastAPI / Celery — see [Configuration](#configuration).

---

## API Reference

### `POST /api/v1/upload`

Accepts a video upload, stores it under a generated UUID (path-traversal safe), enqueues a Celery task, and returns immediately.

**Request:** `multipart/form-data` with a `file` field

**Response:**
```json
{
  "task_id": "a3f9c2d1-4b5e-...",
  "status": "queued",
  "filename": "meeting.mp4"
}
```

### `WS /api/v1/ws/{task_id}`

Connect after uploading. The server first checks the Redis result cache (so late connections still receive completed results), otherwise subscribes to `task:{task_id}` and pushes one message when processing finishes, then closes.

**Push message (success):**
```json
{
  "status": "completed",
  "task_id": "a3f9c2d1-4b5e-...",
  "summary": "The recording discusses..."
}
```

**Push message (failure):**
```json
{
  "status": "error",
  "task_id": "a3f9c2d1-4b5e-...",
  "error": "Audio file duration exceeds 30 minutes limit..."
}
```

### `GET /metrics`

Prometheus scrape endpoint, exposed via `prometheus-fastapi-instrumentator`.

---

## Observability & Load Testing

- **Prometheus** scrapes FastAPI `/metrics` every 15 s (`monitoring/prometheus.yml`, embedded in `k8s/prometheus.yml`).
- **Grafana** ships with a prebuilt pipeline dashboard (`monitoring/grafana/dashboards/pipeline.json`).

Run a load test against the kind deployment (expects a sample file at `test/videos/test_eng.mp3`):

```bash
locust -f locustfile.py
# Locust UI → http://localhost:8089, target host http://localhost:8080
```

Use this to watch the Celery HPA scale workers from 1 to 5 replicas as CPU crosses 70 %.

---

## Model Management & Canary Releases

**MLflow** tracks Whisper model candidates so model selection is data-driven rather than anecdotal - real WER against a fixed, labeled eval set, not just latency:

```bash
python model_eval/prepare_eval_set.py          # one-time: builds a fixed 5-clip labeled eval set from LibriSpeech dev-clean
python model_eval/register_model.py --sizes tiny base small   # benchmarks, logs, and registers each candidate
python model_eval/promote_model.py             # re-verifies @staging's WER and promotes it to @production
```

Each run actually transcribes every clip in the eval set and logs `model_size`, `device`, `compute_type`, real-time factor (RTF), and word error rate (WER) against ground-truth transcripts. `register_model.py` registers every candidate as a model version and promotes the best one (lowest WER within an RTF budget) to the `@staging` alias; `promote_model.py` re-checks that WER and moves `@production` to point at it - the tradeoff curve behind picking `base` is in the MLflow UI, not just asserted in this README.

**Canary deployment** uses native Kubernetes label selectors — no service mesh required. The pattern is fully set up in this repo but **not applied by default** — the stable deploy above only runs `k8s/whisper.yml` (`base` model). This keeps the default footprint to one Whisper deployment instead of two.

If you want to see the canary pattern running:

```bash
# Build a "small" model image (see Getting Started for the "base" build)
docker build -f Dockerfile.inference --build-arg WHISPER_MODEL_SIZE=small -t asyncvtp-whisper-service:small .
kind load docker-image asyncvtp-whisper-service:small

# Apply the canary deployment alongside the stable one
kubectl apply -f k8s/whisper-canary.yml
```

- `k8s/whisper.yml` runs the stable model (image tag `:base`).
- `k8s/whisper-canary.yml` runs the candidate (image tag `:small`).
- Both carry the label `app: whisper`, so the `whisper-service` ClusterIP Service load-balances inference traffic across whichever stable and canary pods currently exist (roughly 50/50 with one replica each).
- **Roll back / clean up** at any time with `kubectl delete -f k8s/whisper-canary.yml` — the stable deployment is unaffected and continues serving all traffic.
- **Promote** the canary by pointing `k8s/whisper.yml` at the `:small` tag, then delete the canary deployment.

---

## CI/CD

`.github/workflows/ci.yml` runs on every push and PR to `main`:

1. **Test** — installs dependencies, lints with `ruff`, and runs `pytest tests/ -v`.
2. **Build-check** *(PRs only)* — builds the FastAPI/Celery and Whisper (`base` model) images without pushing, as a sanity check before merge.
3. **Build & push** *(pushes to `main` only, after tests pass)* — builds and pushes to GitHub Container Registry:
   - `ghcr.io/<owner>/fastapi:latest`
   - `ghcr.io/<owner>/celery:latest`
   - `ghcr.io/<owner>/whisper:latest` and `ghcr.io/<owner>/whisper:base` (same image, two tags — the Whisper build always bakes in the `base` model via `WHISPER_MODEL_SIZE=base`; the canary's `small`-model image is not built in CI, see [Model Management & Canary Releases](#model-management--canary-releases))

---

## Configuration

| Variable | Default (local) | Purpose |
|---|---|---|
| `BROKER_URL` | `redis://localhost:6379/0` | Celery broker + Redis pub/sub |
| `REDIS_PASSWORD` | `change-me-dev-only` | Redis `requirepass`; injected into `BROKER_URL` at runtime |
| `WHISPER_URL` | `http://localhost:3000` | BentoML Whisper service endpoint |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |

The Whisper model size is a **build-time** choice, not a runtime env var — see `WHISPER_MODEL_SIZE` build arg in [Getting Started](#getting-started) and [Model Management & Canary Releases](#model-management--canary-releases). Baking the model into the image at build time avoids re-downloading it on every pod restart.

In Docker Compose these are injected per service; in Kubernetes they live in the `pipeline-config` ConfigMap (`k8s/configmap.yml`), with `REDIS_PASSWORD` sourced from the `redis-secret` Secret. A local `.env` (gitignored) holds `BROKER_URL` for bare-metal development.

---

## Testing ( Under Construction )

```bash
pytest tests/ -v
```

---

## Repository Structure

```
AsyncVTP/
├── app/                          # FastAPI application (producer)
│   ├── main.py                   # App setup, routers, static UI, /metrics
│   ├── routes/
│   │   ├── upload.py             # POST /api/v1/upload
│   │   └── websocket.py          # WS /api/v1/ws/{task_id} + Redis pub/sub bridge
│   └── schemas/task.py           # Pydantic response models
├── worker/                       # Celery application (consumer)
│   ├── celery_app.py             # Celery instance, broker/backend config
│   └── tasks.py                  # process_video: ffmpeg → Whisper → LLM → publish
├── inference/
│   └── whisper_service.py        # BentoML WhisperService (faster-whisper, CPU/int8)
├── k8s/                          # Kubernetes manifests
│   ├── fastapi.yml               # API Deployment + NodePort Service
│   ├── celery.yml                # Worker Deployment (resource requests/limits)
│   ├── hpa.yml                   # Celery HPA: 70 % CPU, 1–5 replicas
│   ├── redis.yml / ollama.yml    # Broker + LLM (with model-weight PVC)
│   ├── whisper.yml               # Stable inference Deployment + Service
│   ├── whisper-canary.yml        # Canary inference (opt-in, not applied by default; image tag :small)
│   ├── prometheus.yml / grafana.yml / mlflow.yml
│   ├── configmap.yml             # pipeline-config env
│   └── pvc.yml                   # Shared uploads volume
├── secrets/                      # Secret templates (real values are gitignored)
│   ├── slack-webhook-url.example
│   ├── redis-password.example
│   └── grafana-admin-password.example
├── monitoring/
│   ├── prometheus.yml            # Scrape configuration
│   └── grafana/dashboards/       # Prebuilt pipeline dashboard
├── model_eval/register_model.py  # Benchmarks Whisper candidates: real WER + RTF, registers + promotes
├── tests/test_upload.py          # API tests (Celery mocked)
├── static/                       # Web UI served by FastAPI
├── .github/workflows/deploy.yml  # CI: test → build → push to GHCR
├── locustfile.py                 # Load test for /api/v1/upload
├── kind-config.yml               # kind cluster with NodePort host mappings
├── Dockerfile                    # FastAPI + Celery image (Python 3.11 + ffmpeg)
├── Dockerfile.inference          # BentoML Whisper image
├── docker-compose.yml            # Full local stack
└── requirements.txt
```

---

## Key Design Decisions

- **Celery over `asyncio` for background work.** `asyncio` provides non-blocking I/O within one process but cannot escape the GIL for CPU-bound work. Celery runs ffmpeg, transcription, and LLM calls in separate processes — a 2-minute job has literally zero impact on API responsiveness.

- **Redis for broker, pub/sub, and result cache.** One infrastructure service bridges producer → worker → WebSocket with no shared memory. Results are also written with `SETEX` (1 h TTL), which eliminates the pub/sub race condition where a client connects *after* the worker has already published.

- **BentoML as a dedicated inference service.** The Whisper model loads once at service startup (not per task) with int8 quantisation on CPU, and is addressable over HTTP. This lets the model be versioned, scaled, monitored, and canary-released independently of the Celery workers — a standard pattern for production inference servers.

- **WebSocket over polling.** The server pushes the moment a result lands. With 500 concurrent users that means 500 silent connections rather than 500 requests per second hammering the API.

- **Canary via native label selectors (opt-in).** Stable and canary inference pods can share a selector behind one ClusterIP Service, giving weighted rollout and instant rollback without Istio/Linkerd overhead — see [Model Management & Canary Releases](#model-management--canary-releases) for how to enable it.

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.