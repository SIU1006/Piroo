# Reproducing a bounded run

Use a dedicated staging deployment, with no rollout or other load during the
observation window. Record actual pod image tags **and digests**, replicas,
worker arguments, resource limits, host/Docker hardware, model versions,
payload duration/hash, client revision/hash, and test parameters in the run's
`environment.json`. Compare deployment images again afterward; a rollout makes
the run unsuitable for a capacity conclusion.

Install the pinned client:

```bash
python -m pip install -r requirements-load-test.txt
```

Before measuring, check that the configured Ollama model exists, the queue is
empty, and one complete upload/transcribe/summarize/WebSocket request succeeds.
Keep that warm-up outside the measured run. A health endpoint alone does not
establish that model inference works.

Forward the API in a separate terminal (this targets one API pod, so it does
not measure API load balancing):

```bash
kubectl --context kind-kind -n asyncvtp-staging port-forward svc/fastapi-service 18080:8000
```

Create a **new directory for each run**. Enable Celery task events for the
observation window, preserving/restoring the workers' prior event settings:

```bash
mkdir -p docs/loadtest/my-run
kubectl --context kind-kind -n asyncvtp-staging exec deploy/fastapi -- python -c 'from worker.celery_app import celery_app; print(celery_app.control.enable_events(reply=True))'
```

Start the event collector in another terminal. Confirm it prints
`capture-ready` before starting Locust. The collector runs inside an application
pod and uses its broker settings without exporting credentials:

```bash
kubectl --context kind-kind -n asyncvtp-staging exec -i deploy/fastapi -- python -u - --seconds 180 < docs/loadtest/capture_events.py > docs/loadtest/my-run/events.jsonl
```

Run three users for 45 seconds, with a maximum 60-second drain period. The
result deadline is 90 seconds from acknowledgement, including WS connection
setup. Keepalives never extend it:

```bash
LOAD_TEST_FILE=model_eval/eval_data/clip_00.wav \
WS_RESULT_TIMEOUT_SEC=90 \
LOAD_TEST_JOBS_JSON=docs/loadtest/my-run/jobs.json \
locust -f locustfile.py --host http://127.0.0.1:18080 \
  --headless -u 3 -r 3 -t 45s --stop-timeout 60 \
  --csv docs/loadtest/my-run/locust --csv-full-history \
  --html docs/loadtest/my-run/locust.html
```

On PowerShell, set those three variables with `$env:NAME = 'value'` before
running Locust. The recorded run used the image built by
`docker build -f docs/loadtest/Dockerfile -t asyncvtp-loadtest:5 .`, with a
repository bind mount and `host.docker.internal:18080` as its target.

After the collector finishes:

```bash
python docs/loadtest/summarize_run.py docs/loadtest/my-run
kubectl --context kind-kind -n asyncvtp-staging exec deploy/fastapi -- python -c 'from worker.celery_app import celery_app; print(celery_app.control.disable_events(reply=True))'
```

Disable events only if they were disabled before the run, and stop the port
forward. The summary uses a **single-process** Locust job ledger; distributed
workers require merging their ledgers before summarizing.

## Reading the evidence

- `jobs.json`: every acknowledged task ID, terminal observation or pending state,
  and keepalive count at one cutoff. A WS timeout/disconnect does not establish
  that the backend task failed.
- `events.jsonl`: Celery start/finish timestamps, joined through the received
  task's application ID. Arguments, media paths and summaries are omitted.
- `per_job.csv`: upload, queue wait bounds, processing, and total latency.
- `summary.json`: counts and sample sizes; missing timings are excluded rather
  than replaced with zero. Pending jobs are excluded from terminal latency.
- `locust_stats.csv`/`locust.html`: HTTP and WS wait metrics, from the same run.
  Locust's periodic CSV can precede the final HTML cutoff; label that snapshot
  explicitly and use the final ledger for the job census.
  Aggregated request totals count both stages and are **not job totals**.

Queue wait includes broker and worker reservation delay. Since the deployed API
does not expose the exact enqueue timestamp, its interval is
`max(0, worker_start - acknowledgement)` to
`max(0, worker_start - upload_start)`. This requires aligned client/worker clocks;
the recorded Docker/kind run shares the same VM clock. Missing start events are
inferred from successful execution runtime and the finish-event timestamp; those
queue intervals are approximate and include small event-dispatch overhead.
`start_source` identifies these samples. Processing uses Celery's success runtime
when available, otherwise elapsed worker start-to-terminal-event time. A retried
job's elapsed fallback includes inference waits and backoff.
Total latency runs from upload start to the client's terminal observation.

Treat a short low-load run as an observed operating point. To establish maximum
sustainable throughput, repeat longer stepped runs with a fixed payload mix and
latency SLO, and require stable backlog plus a completed/failed/pending census.
