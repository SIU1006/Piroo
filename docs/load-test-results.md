# Load Test Results

## Current bounded measurement — 2026-09-16

The corrected load client ignores `queued`/`processing` messages and waits for a
terminal result or one overall deadline. Accepted jobs remain in a final ledger
even when the client times out, disconnects, or stops. Client waiting errors are
not evidence that the backend job failed.

**Run:** [20260916-kind-staging-3u-45s-ready](loadtest/20260916-kind-staging-3u-45s-ready/summary.json).
Three closed-loop users submitted for 45 seconds, with up to 60 seconds to drain.
The actual observation lasted 68.5 seconds, from 07:31:10 to 07:32:18 UTC.
The WS deadline was 90 seconds from upload acknowledgement, including connection
setup. One successful full-pipeline warm-up request was excluded.

### Final job census

| Accepted uploads | Completed | Failed | Still pending | Upload failures |
|---|---|---|---|---|
| 5 | 5 | 0 | 0 | 0 |

All five acknowledged task IDs have terminal client observations and successful
Celery events. Three `processing` keepalives were observed across three jobs;
none was counted as a failure. Broker queue depth was zero before and after the
run, and no worker tasks remained active afterward.

### Latency

| Measurement | Samples | Mean | Median | Maximum |
|---|---|---|---|---|
| Upload start → acknowledgement | 5 | 36.4 ms | 32.2 ms | 54.6 ms |
| Queue wait lower estimate | 5 | 0 ms | 0 ms | 0 ms |
| Queue wait upper estimate | 5 | 34.2 ms | 29.5 ms | 53.2 ms |
| Worker processing | 5 | 34.16 s | 34.59 s | 47.88 s |
| Upload start → terminal result | 5 | 34.19 s | 34.62 s | 47.92 s |

Queue wait is an **estimated interval**, not an exact enqueue measurement. The
enqueue happened between upload start and acknowledgement; jobs could start
before the acknowledgement reached the client. One start came from a
`task-started` event; four missing start events were inferred from success-event
time minus Celery execution runtime. Those four estimates also include small
event-dispatch overhead. Client and workers share Docker's VM clock. Processing
uses Celery's measured runtime and includes waits inside Whisper/Ollama calls.
Total latency ends at the client's terminal observation. Full samples and timing
sources are in [per_job.csv](loadtest/20260916-kind-staging-3u-45s-ready/per_job.csv).
With only five samples, the reported p95 in `summary.json` is simply the maximum.

### Environment and provenance

- **Application image revision:** `162a94c2b0f2fa64a5c1d7f47d0bfff294ed77a1`
  for FastAPI, Celery, and Whisper. Actual image digests and pod identities were
  unchanged between the [before snapshot](loadtest/20260916-kind-staging-3u-45s-ready/environment.json)
  and [after snapshot](loadtest/20260916-kind-staging-3u-45s-ready/environment-after.json).
- **Cluster:** local kind (`kind-kind`), namespace `asyncvtp-staging`, CPU inference.
- **Host:** AMD Ryzen 5 7600X, 6 cores / 12 logical processors, 31.2 GiB RAM.
  Docker had 12 CPUs and 23.5 GiB RAM available. The kind cluster also hosted
  production and monitoring services, so this is a shared-machine measurement.
- **Deployment:** 2 API replicas (each limited to 0.5 CPU / 512 MiB), 2 Celery
  replicas (each 1 CPU / 1 GiB, threads pool, concurrency 4), 1 Whisper service
  (2 CPU / 2 GiB), 1 Ollama service (2 CPU / 4 GiB).
- **Models:** Whisper `small`, CPU/int8; Ollama `llama3.2:latest`, model ID
  `a80c4f17acd5`, 2.0 GB. [Recorded model output](loadtest/20260916-kind-staging-3u-45s-ready/models.txt).
- **Payload:** `model_eval/eval_data/clip_00.wav`, 6.94 seconds, mono 16 kHz WAV;
  exact byte count and SHA-256 are in the environment snapshot.
- **Client:** Locust 2.46.5 and websocket-client 1.9.0. Checkout base commit
  `f737af354ee0e05d9869ae7aaa03230984eb1257` plus the local client repair;
  executed `locustfile.py` SHA-256
  `1197ac41ccc3f896396a34a8a85d9cfe6f0f55bdd416e87e794bd43dd8650c64`.
- **Transport:** Docker client → temporary host port forward → one staging API
  pod. This exercises the pipeline but does not measure ingress or API replica
  load balancing. [Reproduction procedure](loadtest/README.md).

### What this establishes

The complete pipeline successfully finished five 6.94-second clips in a
68.5-second observation, including drain: approximately **0.073 completed jobs/s
(4.4/min)**. At this operating point most elapsed time was inside worker
processing, and measured broker/worker queue delay was small.

This short, closed-loop, single-payload run does **not** establish maximum
sustainable throughput or a concurrent-user ceiling. Larger capacity claims
require repeated longer runs with stepped arrival rates, a fixed payload mix,
latency targets, stable backlog, and the same final job census. The README's
unqualified “under 200 ms” claim has been removed: these uploads were fast, but
older measurements averaged roughly two seconds under different conditions.

### Export cutoffs

| Artifact | Upload requests | Terminal WS observations | Interpretation |
|---|---|---|---|
| [Final HTML](loadtest/20260916-kind-staging-3u-45s-ready/locust-final-5uploads-5completed.html) | 5 | 5 completed | Final run report; matches the job ledger |
| [Periodic stats CSV](loadtest/20260916-kind-staging-3u-45s-ready/locust-periodic-5uploads-4completed_stats.csv) | 5 | 4 completed | Scheduled snapshot at 07:32:18 UTC, just before the last result |
| [Final ledger](loadtest/20260916-kind-staging-3u-45s-ready/jobs.json) / [per-job CSV](loadtest/20260916-kind-staging-3u-45s-ready/per_job.csv) | 5 accepted | 5 completed | Authoritative final job census at one cutoff |

Locust's aggregate total counts HTTP and WS stages, not distinct jobs. Periodic
CSV exports can precede the final HTML even within the same run.

## Failed preliminary run — excluded from capacity evidence

[20260916-kind-staging-3u-45s-missing-model](loadtest/20260916-kind-staging-3u-45s-missing-model/summary.json)
acknowledged 11 jobs: 0 completed, 11 failed, 0 pending at the client cutoff.
There were 12 attempted uploads; one timed out, leaving its acceptance unknown.
All observed task failures were `model 'llama3.2' not found` (404). A concurrent
GitOps rollout also interrupted the event collector and port forward, leaving
no usable queue/processing timings. The initial image snapshot therefore does
not establish one deployed revision for this run. It is retained as failure
evidence, not a capacity measurement. After the configured model was pulled,
the full preflight succeeded and the stable run above was performed.

## Historical snapshots — 2026-09-04

These exports used the older one-message client and lack a final job census,
deployed revision, and timing provenance. They cannot establish successful
end-to-end capacity or support the old 500/1000-user headline.

| Snapshot | Upload requests | Completed WS observations | WS error observations |
|---|---|---|---|
| [HTML, 13:02:30–13:11:07 UTC](loadtest/legacy-html-1074uploads-25errors.html) | 1074 | 49 | 25 |
| [Request CSV, later snapshot](loadtest/legacy-requests-1234uploads-185errors.csv) | 1234 | 49 | 185 |
| [Failure CSV, through 13:13:08 UTC](loadtest/legacy-failures-188errors.csv) | Not recorded | Not recorded | 188 |
| [Empty exceptions export](loadtest/legacy-exceptions-empty.csv) | Not recorded | Not recorded | Not recorded |

The HTML's aggregate count is 1148; the request CSV's is 1468. Their differing
totals represent different cutoffs. WS errors can include client waiting
failures and are not a count of failed backend jobs. Unobserved uploads cannot
be classified as completed or failed from these exports. The older charts and
[historical narrative](load-test-results-20260904-legacy.md) are retained as
archival artifacts and superseded by the measurement above.
