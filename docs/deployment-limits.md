# Supported deployment limits — portfolio v1

## Storage and placement

EKS uses a **private S3 bucket per environment**. An accepted task carries
`s3://bucket/uploads/task-id.ext`, not the API pod's local file path. Each worker
downloads the source into its own `/app/uploads` scratch directory. API and
worker pods can therefore run on different nodes without a shared upload PVC.
Inference services receive audio over HTTP and mount no upload volume.

Kubernetes `ReadWriteOnce` allows several pods on one node, not arbitrary
multi-node mounts. See the [Kubernetes access-mode documentation](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes).
Redis, MLflow and Ollama still use separate RWO volumes, with one replica and
`Recreate` rollouts. They are **not highly available**: an unavailable node or
volume can interrupt the service until recovery. Changing worker replicas does
not increase Whisper/Ollama capacity or prove a higher throughput limit.

Compose and the development Helm overlay use pinned, single-instance MinIO
with a private data volume and throwaway credentials. This is a local S3 API
demonstration, not a production storage cluster. The
[official container guide](https://min.io/docs/minio/container/operations/install-deploy-manage/deploy-minio-single-node-multi-drive.html)
uses the Quay registry. The app's optional `local` storage backend supports
tests/direct development on one shared filesystem only; the deployment chart
always selects S3.

Source objects survive retries. Terminal attempts delete them; failed deletions
and interrupted multipart uploads have a one-day bucket lifecycle backstop.
S3 expiry is asynchronous. Worker scratch is removed after every remote-source
attempt. A terminated pod loses its scratch directory; a later retry downloads
the source again. `emptyDir.sizeLimit` is 10 GiB per API/worker pod. Admission
bounds jobs, not aggregate bytes; disk exhaustion still returns a failure.

## Admission, states and retention

`GET /api/v1/tasks/{id}` returns the same Redis-backed state across API replicas:

| State | Meaning |
|---|---|
| `queued` | Upload is being persisted, waiting for a worker, or waiting for a retry |
| `running` | An active worker is processing the task |
| `completed` | A cached summary is available |
| `failed` | A terminal pipeline failure or queue deadline was recorded |

Queued responses include `phase: uploading`, `waiting` or `retrying`, plus
creation/update timestamps. Completed responses include `summary`; failed
responses include `error`. A WebSocket keeps its existing `completed`/`error`
terminal protocol, so existing clients continue working. A WebSocket timeout
ends only that connection; poll the task endpoint to discover the job's state.
Unknown/expired IDs return 404, malformed UUIDs 422, and unavailable Redis 503.
Results and terminal states expire after one hour; this is not a permanent job
history. Redis persistence/recovery remains necessary to preserve queued jobs.

| Default limit | Value / behavior |
|---|---|
| Outstanding jobs per environment | 100, including uploads, queued, running and retries |
| Full admission | HTTP 429 with `Retry-After: 30`, before app-level file saving/enqueueing |
| API upload-copy deadline | 120 seconds |
| Queue wait per attempt | 600 seconds; expiry checked by the once-per-minute beat sweeper |
| Input size and duration | 1 GiB and 30 minutes |
| Worker attempts | Initial attempt plus at most three transient retries |
| Processing deadline observation | 1,900 seconds plus 120 seconds grace; a live lease prevents false expiry |
| Terminal cache retention | 3,600 seconds |

Admission uses a Redis Lua script, so simultaneous API producers cannot exceed
the job limit. Completion/failure atomically stores the result, changes state,
releases admission and publishes to WebSocket subscribers. Terminal decisions
are immutable while their cache exists. A stalled queued job is failed and its
source removed by the sweeper; a late delivery cannot start after that result.
Run **one beat scheduler per environment**. If beat stops, stale reservations
remain counted and admission eventually closes; it does not silently accept
an unbounded queue.
S3 worker deliveries also require their outstanding reservation to still exist,
so a late delivery after failed-upload cancellation cannot reopen admission.

Configure `MAX_OUTSTANDING_TASKS`, `TASK_QUEUE_TIMEOUT_SECONDS` and
`UPLOAD_TIMEOUT_SECONDS`, or the chart's `admission.*` values. Queue/upload
deadlines must be shorter than the four-hour nonterminal-state retention.
The current Celery threads pool does **not enforce a process-killing hard time
limit**. A hung live attempt can retain its lease/admission until operator
recovery; the deadline is not a cancellation guarantee. Service HTTP timeouts
and queue admission are enforced independently. Broker publication and object
storage are separate operations, not an exactly-once transaction; a lost broker
acknowledgement can leave an unacknowledged job. Drain jobs before incompatible
rollouts and recover unavailable Redis before accepting more uploads.

Multipart parsing happens before the handler. Configure ingress request-body
and connection/time limits as well; job admission is not a network rate limiter.
`/healthz` is process liveness; `/readyz` checks Redis and access to the upload
bucket. This version supports private portfolio demos. Authentication,
per-user authorization for task IDs, rate limits and HTTPS must be added before
public exposure. Compose binds existing exposed services to localhost.

## One external-metrics owner

On the shared staging/production cluster, **`asyncvtp-prod` owns the adapter,
its RBAC and `v1beta1.external.metrics.k8s.io` APIService**. Staging disables the
adapter and uses that same aggregated API. Production Prometheus scrapes its
own annotated pods plus staging's Redis-exporter Service for queue depth. The
adapter queries `max by (namespace)` with the requested namespace matcher, so
an HPA in staging cannot consume production's queue measurement. Both HPAs
retain their separate CPU and queue-depth targets. There is no APIService
`ignoreDifferences` rule to hide competing ownership.

Production Prometheus, its adapter and its serving-certificate Secret must be
healthy before either environment's external-metric HPA can operate. The
certificate Secret is required **only in `asyncvtp-prod`**. Staging's queue HPA
therefore shares this availability dependency. For a staging-only cluster,
explicitly assign the singleton to that release and its Prometheus; never
enable two owners in one cluster. The chart's self-signed aggregated API TLS
configuration remains a portfolio simplification.

## EKS configuration and migration

1. Review/apply `terraform/uploads.tf` with the existing infrastructure. It
   creates private encrypted buckets, lifecycle policies and IRSA roles scoped
   to each environment's `asyncvtp-uploads` ServiceAccount. Bucket destruction
   refuses nonempty buckets (`force_destroy = false`).
2. Read `terraform output -json upload_storage`. In each tracked environment
   overlay, set `objectStorage.bucket`, `objectStorage.region`, and
   `objectStorage.serviceAccount.roleArn` from that environment's output.
   Leave endpoint/credentials Secret empty for AWS. Blank buckets deliberately
   fail Helm rendering until configured; CI uses validation-only values.
3. On an existing deployment, pause incoming uploads and drain queued/active
   jobs using the old images before deploying these images. Do not prune an
   uploads PVC containing unfinished jobs. Retain/export any needed data
   before removing it; this branch does not migrate old filesystem jobs.
4. For adapter ownership migration, pause automatic sync on both Argo apps
   and record the current APIService/adapter state. Keep staging unsynced while
   transferring APIService tracking to production and syncing production as
   its singleton owner. Confirm the APIService points to `asyncvtp-prod` and
   its Argo tracking metadata identifies `asyncvtp-prod`. Preview staging's
   prune operation and exclude the shared APIService until that ownership is
   confirmed, then remove staging's old adapter resources. Confirm the external
   API is available before resuming both apps.
5. Verify both environment namespace queries and task status before reopening
   uploads. These are deployment steps; no cloud resources were applied by
   this change.

```bash
kubectl get apiservice v1beta1.external.metrics.k8s.io
kubectl get --raw /apis/external.metrics.k8s.io/v1beta1/namespaces/asyncvtp-staging/celery_queue_depth
kubectl get --raw /apis/external.metrics.k8s.io/v1beta1/namespaces/asyncvtp-prod/celery_queue_depth
curl http://localhost:8000/readyz
curl http://localhost:8000/api/v1/tasks/REPLACE_WITH_ACCEPTED_UUID
```

The integration suite checks real Redis admission races, S3 upload/download
across independent producer/worker directories, retry recovery and cleanup,
terminal immutability, queue expiry and all four API states. Inference is
stubbed in the storage-focused tests; separate integration tests exercise the
real BentoML contract. Helm CI checks single adapter ownership and removal of
shared upload PVC mounts. These checks establish the boundaries, not a
multi-node EKS capacity measurement.

Local validation on 2026-09-16: **68 unit tests and 19 real-service integration
tests passed**. Helm lint and the staging/production ownership validator passed;
Terraform formatting/validation and GitHub Actions syntax checks passed. The
development bucket initializer also completed against the isolated MinIO
service. Shared Kubernetes deployments and AWS resources were not changed.
