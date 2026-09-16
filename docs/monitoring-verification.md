# Verify monitoring and alert delivery

CI renders the dev, staging, and production Helm overlays, rejects duplicate YAML keys,
checks the mounted Prometheus rule file with the chart's pinned `promtool`, checks
Alertmanager routes with the pinned `amtool`, and runs synthetic alert tests. The
fixtures in [`monitoring/alerts.test.yml`](../monitoring/alerts.test.yml) cover
task failures, retries, stale canary checks, and recovery. The chart mounts the
rules into Prometheus and points it to `alertmanager-service:9093`.

## Exercise Prometheus → Alertmanager in a local kind cluster

Use a running local dev release with the dev overlay and local images loaded as
described in the main README. These commands assume release `asyncvtp` in
namespace `asyncvtp-dev`; adjust both names for your cluster. The dev overlay
routes to a `local` receiver that records alerts in Alertmanager without
sending notifications to Slack.

1. Start port forwards in separate terminals:

   ```sh
   kubectl -n asyncvtp-dev port-forward svc/prometheus-service 9090:9090
   kubectl -n asyncvtp-dev port-forward svc/alertmanager-service 9093:9093
   ```

   Open <http://localhost:9090/targets> and <http://localhost:9090/rules>.
   Check that Celery is `UP` and four `asyncvtp-slos` rules are loaded. Confirm
   the local route at <http://localhost:9093/#/status>.

2. Enable the opt-in test rule and set its value to 1. Apply the same values
   you use for the existing release; this example uses the dev overlay:

   ```sh
   helm upgrade asyncvtp k8s -n asyncvtp-dev -f k8s/values-dev.yaml \
     --set prometheus.smokeAlert.enabled=true --set prometheus.smokeAlert.value=1
   kubectl -n asyncvtp-dev rollout status deployment/prometheus
   ```

   `AsyncVTPSmokeAlert` should appear as **FIRING** at
   <http://localhost:9090/alerts> and then at <http://localhost:9093/#/alerts>
   after Alertmanager's 30-second group wait. The API views are
   <http://localhost:9090/api/v1/alerts> and
   <http://localhost:9093/api/v2/alerts>.

3. Set the same rule's value to 0, which gives Prometheus a resolved
   evaluation and sends the resolution to Alertmanager. Wait for rollout and
   inspect the same API views:

   ```sh
   helm upgrade asyncvtp k8s -n asyncvtp-dev -f k8s/values-dev.yaml \
     --set prometheus.smokeAlert.enabled=true --set prometheus.smokeAlert.value=0
   kubectl -n asyncvtp-dev rollout status deployment/prometheus
   ```

   The test alert clears from the active list. Then remove the opt-in rule:

   ```sh
   helm upgrade asyncvtp k8s -n asyncvtp-dev -f k8s/values-dev.yaml
   ```

For a portfolio demo, capture the rules page, a Celery `UP` target, the firing
alert in both services, and the cleared active alert list. Do not enable Slack
notifications in the dev overlay during this exercise.

## Check the real canary and task metrics

Celery beat enqueues `check_canary_wer` hourly. Trigger one immediately through
the same queue (this must execute inside the running worker so its gauges are
scraped from that process):

```sh
kubectl -n asyncvtp-dev exec deployment/celery -- \
  python -m celery -A worker.tasks call check_canary_wer
kubectl -n asyncvtp-dev logs deployment/celery --since=10m
```

At <http://localhost:9090/graph>, query `canary_wer` and
`canary_last_success_timestamp_seconds`. The timestamp should advance only
after every reference clip transcribes successfully. Query
`celery_task_total{task_name="process_video"}` after a successful and a failed
job to see the terminal counters used by the failure rate alert. Intermediate
retry attempts do not enter its denominator.

In staging and production the default route is Slack. It reads the URL from
`alertmanager-secret` key `slack-webhook-url`; those environments require that
Secret to be provisioned before deploying Alertmanager.
