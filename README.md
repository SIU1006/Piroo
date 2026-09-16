# AsyncVTP — Async Video-to-Text Pipeline

A ML inference pipeline: upload a video, receive an AI-generated summary in real time. Built incrementally from a single FastAPI endpoint into a fully observable, autoscaling Kubernetes deployment with self-hosted inference, canary releases, and CI/CD.

---

## Note
This project is still in beta. I am trying to polish it and write more tests so that it can be fully functionable and bug-free. I will remove this section when it is ready to be used.

This is my first MLOPS related project. I am trying my best to work it through and learn as much as possible. A star would be huge motivation to me!

---

## Why This Architecture

Transcribing a 50 MB video inside a standard HTTP request guarantees browser timeouts and blocked server threads. The solution is to **decouple job ingestion from job execution**:

- **FastAPI** acknowledges accepted uploads with a `task_id`; measured latency and run conditions are recorded in [Load Test Results](docs/load-test-results.md).
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

- Docker Desktop with Docker Compose
- Git
- For the Kubernetes path: `kubectl`, [kind](https://kind.sigs.k8s.io/), and [Helm](https://helm.sh/docs/intro/install/)

### Option A — Docker Compose (local development)

```bash
git clone https://github.com/SIU1006/Piroo.git
cd Piroo
docker compose up --build --wait
```

The first run builds both application images, downloads the Whisper `base`
model into the inference image, and runs a one-shot `ollama-model` service that
pulls `llama3.2`. It can take several minutes and requires several gigabytes of
free disk space. Follow startup or stop the stack with:

```bash
docker compose logs -f
docker compose down
```

To use a custom Redis password or model, copy `.env.example` to `.env` and edit
it before starting. The same Redis password is passed to the Redis server,
healthcheck, API, worker, and beat scheduler.

| Service | URL |
|---|---|
| Web UI / API | http://localhost:8000 (`/docs` for Swagger) |
| Whisper inference service | http://localhost:3000 |
| Ollama | http://localhost:11434 |

### Option B — Kubernetes (kind cluster)

Create the cluster with host port mappings preconfigured:

```bash
kind create cluster --name asyncvtp --config kind-config.yml
```

Build every application image from this checkout. The Whisper build downloads
and embeds the selected model, so the pod does not need a model cache or network
access at runtime:

```bash
docker build --pull -t fastapi:latest -t celery:latest .
docker build --pull -f Dockerfile.inference \
  --build-arg WHISPER_MODEL_SIZE=base -t whisper:base .
kind load docker-image --name asyncvtp fastapi:latest celery:latest whisper:base
```

The dev overlay creates disposable Redis and Grafana Secrets, disables external
Slack delivery, and disables the HPA adapter. Install it into a dedicated
namespace:

```bash
helm lint k8s
helm upgrade --install asyncvtp k8s \
  --namespace asyncvtp-dev --create-namespace \
  -f k8s/values-dev.yaml

Wait for ollama to be ready, then pull the model:
kubectl wait -n asyncvtp-dev --for=condition=available --timeout=180s deployment/ollama
kubectl exec -n asyncvtp-dev deployment/ollama -- ollama pull llama3.2

Wait for the application workloads:
kubectl wait -n asyncvtp-dev --for=condition=available --timeout=300s \
  deployment/fastapi deployment/celery deployment/celery-beat deployment/whisper-service
kubectl get pods -n asyncvtp-dev
```

Useful lifecycle commands:

```bash
# Preview without installing
helm template asyncvtp k8s --namespace asyncvtp-dev -f k8s/values-dev.yaml

# Rebuild and reload changed images before upgrading
kind load docker-image --name asyncvtp fastapi:latest celery:latest whisper:base
helm upgrade asyncvtp k8s --namespace asyncvtp-dev -f k8s/values-dev.yaml

# Remove the release and cluster
helm uninstall asyncvtp --namespace asyncvtp-dev
kind delete cluster --name asyncvtp
```

> The Whisper model is baked into the image at build time via the `WHISPER_MODEL_SIZE` build arg (default `base`), so it doesn't need to be downloaded again on every pod restart. If you also want to build the canary's `small`-model image, see [Model Management & Canary Releases](#model-management--canary-releases).


| Service | URL |
|---|---|
| FastAPI (NodePort 30000) | http://localhost:8080 |
| Prometheus (NodePort 30090) | http://localhost:9090 |
| Grafana (NodePort 30030) | http://localhost:3001 (`admin` / `dev-only-admin-password`) |
| MLflow (NodePort 30050) | http://localhost:3002 |

> **Note:** the Celery HPA requires [metrics-server](https://github.com/kubernetes-sigs/metrics-server) in the cluster (not shipped with kind by default). Verify with `kubectl top pods`, then watch autoscaling under load via `kubectl get hpa -w`.

---

## Secrets

The dev overlay creates disposable values automatically. For staging or
production, use the platform's secret manager or the included setup script.
The script prompts without echoing input, renders the chart's Secret names and
keys, applies them to the requested namespace, and deletes its temporary files:

```bash
./k8s/setup-secrets.sh --namespace asyncvtp-staging --values values-staging.yaml
# Windows PowerShell equivalent:
./k8s/setup-secrets.ps1 -Namespace asyncvtp-staging -ValuesFile values-staging.yaml
```

| Secret | Key | Used by |
|---|---|---|
| `alertmanager-secret` | `slack-webhook-url` | Alertmanager Slack receiver |
| `redis-secret` | `redis-password` | Redis `requirepass`, consumers, and the exporter |
| `grafana-secret` | `admin-password` | Grafana admin login |

All files under `secrets/` except `*.example` are ignored by Git and the entire
directory is excluded from Docker build contexts. Local secret files therefore
cannot enter an application image. The chart injects the same Redis Secret into
the server, health checks, API, workers, and exporter.

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

If the Redis task queue is unavailable, the API returns HTTP 503 and removes the saved upload.

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

- **Prometheus** scrapes annotated application pods every 15 s using the configuration in `k8s/templates/monitoring/prometheus-configmap.yaml`.
- **Grafana** ships with the dashboard in `k8s/templates/monitoring/grafana-dashboard.yaml`.
- **Alertmanager** receives the rules in `k8s/templates/monitoring/alert-rules.yaml`. See `docs/monitoring-verification.md` for a firing-and-resolution demo.

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

**Canary deployment** uses native Kubernetes label selectors — no service mesh required. The chart keeps it disabled by default, so the setup above runs only the stable `base` deployment.

If you want to see the canary pattern running:

```bash
# Build a "small" model image (see Getting Started for the "base" build)
docker build --pull -f Dockerfile.inference \
  --build-arg WHISPER_MODEL_SIZE=small -t whisper:small .
kind load docker-image --name asyncvtp whisper:small

# Enable the chart's canary Deployment alongside the stable one
helm upgrade asyncvtp k8s --namespace asyncvtp-dev -f k8s/values-dev.yaml \
  --set whisper.canary.enabled=true
```

- `k8s/templates/whisper/deployment.yaml` runs the stable model (`whisper:base`).
- `k8s/templates/whisper/canary.yaml` runs the candidate (`whisper:small`) when enabled.
- Both carry the label `app: whisper`, so the `whisper-service` ClusterIP Service load-balances inference traffic across whichever stable and canary pods currently exist (roughly 50/50 with one replica each).
- **Roll back** by running the same Helm upgrade with `--set whisper.canary.enabled=false`; the stable deployment continues serving traffic.
- **Promote** by changing the stable `whisper.image.tag` value after its evaluation gate passes, then disable the canary.

---

## CI/CD

`.github/workflows/ci.yml` runs on every push and PR to `main`:

1. **Test** — installs dependencies, lints with `ruff`, and runs the default pytest suite.
2. **Integration** — builds the application and a tiny Whisper image, starts disposable Redis and BentoML services, and exercises the real service boundaries.
3. **Helm and Terraform validation** — renders/lints the Helm chart, checks Terraform formatting, and validates the Terraform configuration without a backend.
4. **Build-check** *(PRs only)* — builds the FastAPI/Celery and Whisper (`base` model) images after all validation jobs pass.
5. **Build & push** *(pushes to `main` only)* — publishes images only after tests, integration tests, model checks, Helm validation, and Terraform validation pass:
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
| `LLM_MODEL` | `llama3.2` | Ollama model pulled at startup and used for summaries |

The Whisper model size is a **build-time** choice, not a runtime env var — see `WHISPER_MODEL_SIZE` build arg in [Getting Started](#getting-started) and [Model Management & Canary Releases](#model-management--canary-releases). Baking the model into the image at build time avoids re-downloading it on every pod restart.

In Docker Compose these values come from one shared environment block. In Kubernetes they live in the `pipeline-config` ConfigMap (`k8s/templates/configmap.yaml`), with `REDIS_PASSWORD` sourced from `redis-secret`. Copy `.env.example` to the gitignored `.env` to override Compose defaults.

---

## Testing

Run the default suite locally:

```bash
python -m pytest tests/ -v
```

Tests marked `integration` are skipped unless `RUN_SERVICE_INTEGRATION=1` is set. CI runs them in the application image against disposable Redis and BentoML containers, so broker publication, Pub/Sub behavior, media extraction, Celery retries, and the Whisper HTTP contract are checked without mocks at those boundaries.

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
├── k8s/                          # Helm chart
│   ├── Chart.yaml
│   ├── values.yaml               # Shared defaults
│   ├── values-dev.yaml           # Fresh kind-cluster overlay
│   ├── setup-secrets.sh / .ps1   # Staging/production Secret setup
│   └── templates/
│       ├── fastapi/              # API Deployment + Service
│       ├── celery/               # Worker, beat scheduler, and HPA
│       ├── whisper/              # Stable and optional canary inference
│       ├── redis/                # Broker and exporter
│       └── monitoring/           # Prometheus, Grafana, and Alertmanager
├── secrets/                      # Secret templates (real values are gitignored)
│   ├── slack-webhook-url.example
│   ├── redis-password.example
│   └── grafana-admin-password.example
├── monitoring/                   # Render validator and Prometheus rule tests
├── model_eval/register_model.py  # Benchmarks Whisper candidates: real WER + RTF, registers + promotes
├── tests/test_upload.py          # API tests (Celery mocked)
├── static/                       # Web UI served by FastAPI
├── .github/workflows/ci.yml      # Test, chart validation, build, and push
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

- **WebSocket updates.** Persistent connections deliver keepalives and terminal results without repeated status polling. This design choice does not establish a concurrent-user capacity; see the [bounded measurement](docs/load-test-results.md).

- **Canary via native label selectors (opt-in).** Stable and canary inference pods can share a selector behind one ClusterIP Service, giving weighted rollout and instant rollback without Istio/Linkerd overhead — see [Model Management & Canary Releases](#model-management--canary-releases) for how to enable it.

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.
