# AsyncVTP — Full Project Description

[← Back to the project overview](../README.md)

AsyncVTP turns uploaded video or audio into a generated summary through an
asynchronous pipeline. This guide covers the implementation, setup and
operational tradeoffs behind the employer-facing overview.

## Contents

- [Architecture and rationale](#why-this-architecture)
- [Local and Kubernetes setup](#getting-started)
- [Secrets](#secrets)
- [API reference](#api-reference)
- [Monitoring and load testing](#observability--load-testing)
- [Model evaluation and releases](#model-management--canary-releases)
- [CI/CD](#cicd), [configuration](#configuration) and [testing](#testing)
- [Repository structure](#repository-structure)

## Supported scope and evidence

This version targets private portfolio demonstrations. Local Compose runs
self-hosted inference and MinIO; the Kubernetes chart supports local kind and
an AWS EKS configuration with S3. AWS deployment requires environment-specific
bucket and IAM settings. Historical EKS evidence does not verify the later
object-storage migration.

The default admission ceiling is 100 outstanding jobs, with a 1 GiB upload
limit, a 30-minute media limit and one-hour result retention. These are
configured limits, not measured throughput guarantees. Stateful services are
single-instance, generated summaries can contain factual errors, and the
Kubernetes worker threads pool cannot enforce a process-killing timeout.
Public deployment requires authentication, authorization, rate limits and HTTPS.
See [supported deployment limits](deployment-limits.md) for the complete scope.

| Evidence or guide | What it establishes |
|---|---|
| [EKS screenshots](evidence/README.md) and [verification](eks-verification.md) | Historical cloud deployment and completed upload demonstration |
| [Load-test results](load-test-results.md) | A bounded five-job run on the recorded earlier revision; not a maximum-capacity claim |
| [Model evaluation](model-evaluation.md) | Recorded local candidate evaluation, promotion, deployment, verification and rollback; summary-quality findings |
| [Monitoring verification](monitoring-verification.md) | Procedure for demonstrating alert firing and resolution |
| [Deployment runbook](demo-runbook.md) and [Terraform guide](../terraform/README.md) | Cloud provisioning and deployment instructions |

## Why This Architecture

Transcription and summarisation can take much longer than an upload request. Running them inside that request ties API capacity to inference latency and risks connection timeouts. The solution is to **decouple job ingestion from job execution**:

- **FastAPI** acknowledges accepted uploads with a `task_id`; measured latency and run conditions are recorded in [Load Test Results](load-test-results.md).
- A **Celery** worker fleet performs the heavy pipeline — ffmpeg audio extraction, Whisper transcription, LLM summarisation — in a separate worker service.
- The result is pushed over **WebSocket**; `GET /api/v1/tasks/{id}` also exposes queued/running/completed/failed state for reconnection and polling.

Job admission is bounded across API replicas. Actual upload and processing
latencies depend on payload, storage and inference capacity.

---

## Architecture

```
Browser
  │
  │  POST /api/v1/upload (video file)
  ▼
FastAPI (Producer) ─────── persists source in S3/MinIO, returns queued task ID
  │                           serves web UI · exposes /metrics for Prometheus
  │  enqueues task
  ▼
Redis ── Celery broker · pub/sub bus · result cache
  │
  │  dequeues task carrying an S3 object reference
  ▼
Celery Worker (Consumer) ── HPA replica limits depend on the environment
  │
  ├── 1. S3 / MinIO          → download source to worker-private scratch
  ├── 2. ffmpeg              → extract mono 128 kbps MP3 (30 min limit)
  ├── 3. BentoML Whisper     → POST /transcribe  (faster-whisper, CPU / int8)
  ├── 4. Ollama llama3.2     → summarise transcript
  └── 5. Redis               → publish result + cache it with 1 h TTL
  │
  │  publishes to channel task:{task_id}
  ▼
FastAPI WebSocket /api/v1/ws/{task_id}
  │
  │  pushes result down the open connection
  ▼
Browser receives the summary in real time

Observability: Prometheus scrapes FastAPI /metrics → Grafana dashboards
```

FastAPI and Celery workers use Redis for job/result messages and object storage
for source media. The Whisper model is served by a standalone BentoML inference
service, so it can be versioned and released independently of the workers.

---

## Features

- **Asynchronous job processing** — Celery + Redis, with atomic admission for at most 100 outstanding jobs per environment
- **Placement-independent uploads** — private S3 buckets on EKS; MinIO locally; workers download sources independently
- **Task status API** — queued/running/completed/failed states and one-hour cached results
- **Self-hosted speech-to-text** — faster-whisper behind a BentoML inference service (CPU, int8 quantised, no GPU or external inference API required)
- **Local LLM summarisation** — Ollama running llama3.2, no external API dependency
- **Real-time delivery** — WebSocket push with a Redis-backed result cache that survives late connections
- **Kubernetes-native** — Deployments, Services, PVCs, ConfigMaps, and a HorizontalPodAutoscaler using queue depth and CPU
- **Canary inference releases (opt-in)** — candidate-only inference and evaluation traffic use a separate Service; see [Model Management & Canary Releases](#model-management--canary-releases)
- **Full observability** — Prometheus metrics, prebuilt Grafana dashboard and Locust for load testing
- **MLOps lifecycle** — MLflow experiment tracking and model registry; GitHub Actions CI that tests, builds, and pushes images to GHCR

---

## Tech Stack

| Component | Technology | Role |
|---|---|---|
| API layer | **FastAPI** | Async upload endpoint, WebSocket gateway, static UI, `/metrics` |
| Task queue | **Celery** | Runs ffmpeg → Whisper → LLM pipeline in a separate worker service |
| Broker / bus | **Redis** | Triple duty: Celery broker, pub/sub channel, result cache (1 h TTL) |
| Upload storage | **S3 / local MinIO** | Private source objects, independently fetched by workers |
| Media processing | **ffmpeg** | Extracts mono 128 kbps MP3 from video; duration probing |
| Speech-to-text | **faster-whisper** via **BentoML** | Dedicated inference service; model loaded once at startup |
| Summarisation | **Ollama / llama3.2** | Local LLM inference, no external inference API required |
| Orchestration | **Kubernetes (kind / EKS)** | Helm deployments; optional HPA using queue depth and CPU |
| Monitoring | **Prometheus + Grafana** | API metrics, dashboards, Celery introspection |
| Load testing | **Locust** | Simulated concurrent uploads against the cluster |
| Experiment tracking | **MLflow** | Logs Whisper model candidates (params, RTF, WER against a labeled eval set), registers + promotes via aliases |
| CI/CD | **GitHub Actions** | pytest gate → Docker builds → push to GHCR |

---

## Getting Started

### Prerequisites

- Docker Desktop with Docker Compose
- Git
- For the Kubernetes path: Bash, OpenSSL, `kubectl`, [kind](https://kind.sigs.k8s.io/), and [Helm](https://helm.sh/docs/intro/install/)

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
Compose initializes a local MinIO bucket and its cleanup lifecycle before API
and workers start; no shared upload volume is needed. See the
[supported deployment limits](deployment-limits.md) for storage, queue
deadlines, adapter ownership, EKS prerequisites and migration steps.

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
kubectl create namespace asyncvtp-dev --dry-run=client -o yaml | kubectl apply -f -
# Development-only self-signed certificate; clients explicitly trust it.
cert_dir="$(mktemp -d)"
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout "$cert_dir/tls.key" -out "$cert_dir/tls.crt" -subj '/CN=minio-service' \
  -addext 'subjectAltName=DNS:minio-service,DNS:minio-service.asyncvtp-dev.svc,DNS:minio-service.asyncvtp-dev.svc.cluster.local' \
  -addext 'basicConstraints=critical,CA:TRUE'
kubectl create secret generic minio-tls --namespace asyncvtp-dev \
  --from-file=tls.crt="$cert_dir/tls.crt" --from-file=tls.key="$cert_dir/tls.key" \
  --from-file=ca.crt="$cert_dir/tls.crt" --dry-run=client -o yaml | kubectl apply -f -
rm -- "$cert_dir/tls.key" "$cert_dir/tls.crt"
rmdir -- "$cert_dir"

helm lint k8s
helm upgrade --install asyncvtp k8s \
  --namespace asyncvtp-dev --create-namespace \
  -f k8s/values-dev.yaml

# Wait for Ollama to be ready, then pull the model:
kubectl wait -n asyncvtp-dev --for=condition=available --timeout=180s deployment/ollama
kubectl exec -n asyncvtp-dev deployment/ollama -- ollama pull llama3.2

# Wait for the application workloads:
kubectl wait -n asyncvtp-dev --for=condition=available --timeout=300s \
  deployment/fastapi deployment/celery deployment/celery-beat deployment/whisper
kubectl get pods -n asyncvtp-dev
```

Useful lifecycle commands:

MinIO uses HTTPS. For other namespaces, provision a certificate with matching
service DNS names in `minio.tlsSecret`; `objectStorage.caSecret` must contain
the issuing CA as `ca.crt`. AWS S3 overlays leave `caSecret` empty to use public
CA trust. When rotating an externally managed credentials or CA Secret, also
bump `objectStorage.credentialsSecretVersion` or `objectStorage.caSecretVersion`
in the release values and reconcile/upgrade Helm. Secret updates alone do not
cause Helm/GitOps to re-render or restart pods.

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
Both helpers render the chart's Secret names and keys and apply them to the
requested namespace. The Bash helper masks input and deletes its temporary
files. The PowerShell helper uses visible prompts and passes values to Helm
as command arguments; use an appropriate secret manager for shared environments:

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

Accepts a media upload, reserves admission, persists it under a generated UUID
in S3/MinIO, then enqueues the object reference and returns its task ID.

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
Full job capacity returns HTTP 429 with `Retry-After: 30`.

### `GET /api/v1/tasks/{task_id}`

Returns `queued`, `running`, `completed` or `failed`. Queued jobs include a phase
(`uploading`, `waiting`, `retrying`) and timestamps. Terminal responses include
the summary or error. Unknown/expired IDs return 404, malformed UUIDs 422, and
unavailable status storage 503. Results expire after one hour.

```bash
curl http://localhost:8000/api/v1/tasks/REPLACE_WITH_ACCEPTED_UUID
```

See [deployment limits](deployment-limits.md) for queue deadlines,
private deployment requirements and the singleton metrics-adapter owner.

### `WS /api/v1/ws/{task_id}`

Connect after uploading. The server subscribes to `task:{task_id}` before checking the result cache, avoiding a gap between subscribing and reading a completed result. It sends `processing` keepalives while waiting, then a terminal result and closes. A connection timeout does not cancel the job; use the task status endpoint to check it.

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
- **Alertmanager** receives the rules in `k8s/templates/monitoring/alert-rules.yaml`. See [monitoring verification](monitoring-verification.md) for a firing-and-resolution demo.

Run a load test against the kind deployment (defaults to the committed `model_eval/eval_data/clip_00.wav`):

```bash
locust -f locustfile.py
# Locust UI → http://localhost:8089, target host http://localhost:8080
```

The dev overlay disables HPA. In staging and production, HPA uses broker queue depth and CPU with environment-specific replica limits. Scaling workers does not increase Whisper or Ollama capacity. See the [measured workload and its limits](load-test-results.md).

---

## Model Management & Canary Releases

Registration, CI, and fresh promotion verification use the exact 20 filenames and decoding settings in `model_eval/evaluation_config.json`. Every evaluation records the full dataset and subset hashes, model revision, configuration, packages, image identity, and per-clip results. Hugging Face revisions are pinned in `model_eval/model_revisions.json` and baked into inference images.

```bash
python -m model_eval.register_model --sizes tiny base small
python -m model_eval.check_regression --output ci-evaluation.json
```

Local registration records `not-deployed`; production promotion requires registration against the actual candidate endpoint and immutable image identity. Promotion sends the examples through that candidate again and rejects dataset/configuration mismatches, WER regression, or an exceeded RTF budget. Changing an MLflow alias authorizes a release; deployment is a separate operation.

The [model evaluation guide](model-evaluation.md) records a complete **candidate evaluation → fresh promotion → deployment → verification → rollback** example, with real model outputs, MLflow versions, Docker image IDs and rollback receipts. It also contains a three-case summary-quality review, including a numerical comparison error found in the generated summaries.

**Canary deployment** is opt-in. Stable traffic uses `whisper-service` (`track: stable`); the checker uses `whisper-canary-service` (`track: canary`). Disabled canaries do not fall back to the stable endpoint. Use a registry manifest digest for the candidate:

```bash
helm upgrade --install asyncvtp k8s -f k8s/values.yaml \
  --set whisper.canary.enabled=true \
  --set whisper.canary.image.digest=sha256:<published-manifest-digest>
```

The candidate's model size comes from its built image. The checker stores its dataset/configuration, model revision, endpoint, image digest and WER in Redis at `canary:evaluation`. Verification traffic is isolated; no user traffic is split automatically. Disable the candidate with `--set whisper.canary.enabled=false`, or deploy a promoted digest through the stable image values after fresh verification.

---

## CI/CD

`.github/workflows/ci.yml` runs on pushes and PRs to `main`, excluding documentation-only changes, and can also be triggered manually:

1. **Test** — installs dependencies, lints with `ruff`, and runs the default pytest suite.
2. **Integration** — builds the application and a tiny Whisper image, starts disposable Redis, MinIO and BentoML services, and exercises the real service boundaries.
3. **Helm and Terraform validation** — renders/lints the Helm chart, checks Terraform formatting, and validates the Terraform configuration without a backend.
4. **Build-check** *(PRs only)* — builds the FastAPI/Celery and Whisper (`base` model) images after unit tests, integration tests, Helm and Terraform validation pass; model evaluation runs as a separate check.
5. **Build & push** *(pushes to `main` only)* — publishes images only after tests, integration tests, model checks, Helm validation, and Terraform validation pass:
   - `ghcr.io/<owner>/fastapi:latest`
   - `ghcr.io/<owner>/celery:latest`
   - `ghcr.io/<owner>/whisper:latest`, `:<model-size>` and `:<commit>` — CI selects the committed baseline's model size, evaluates its built image, then publishes that exact saved image. Evaluation and publication digests are retained as workflow artifacts.

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

Run the default suite locally with Python 3.11 and ffmpeg installed:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Tests marked `integration` are skipped unless `RUN_SERVICE_INTEGRATION=1` is set. CI runs them in the application image against disposable Redis, MinIO and BentoML containers, so broker publication, Pub/Sub behavior, media extraction, Celery retries, and the Whisper HTTP contract are checked without mocks at those boundaries.

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

- **Celery for background work.** Ingestion and execution run in separate services, with bounded admission and independently measurable upload/processing latency.

- **Redis for broker, pub/sub, and result cache.** One infrastructure service bridges producer → worker → WebSocket with no shared memory. An atomic Redis script caches terminal results for one hour, updates task state, releases admission and publishes the result. Cached results support clients that reconnect after completion.

- **BentoML as a dedicated inference service.** The Whisper model loads once at service startup (not per task) with int8 quantisation on CPU, and is addressable over HTTP. This lets the model be versioned, scaled, monitored, and canary-released independently of the Celery workers — a standard pattern for production inference servers.

- **WebSocket updates.** Persistent connections deliver keepalives and terminal results without repeated status polling. This design choice does not establish a concurrent-user capacity; see the [bounded measurement](load-test-results.md).

- **Canary via native label selectors (opt-in).** Separate stable and candidate Services isolate evaluation traffic; deployment and rollback select explicit image identities. See [Model Management & Canary Releases](#model-management--canary-releases).

---

## License

Distributed under the MIT License. See [LICENSE](../LICENSE) for details.
