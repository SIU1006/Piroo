"""Load test for the AsyncVTP pipeline.

Exercises the *full* path a real browser takes, not just the upload ack:

  1. POST /api/v1/upload          -> queued ack with a task_id
  2. WS  /api/v1/ws/{task_id}     -> wait for the completed/error result

Both stages are timed separately so the report can distinguish
"ingestion bottleneck" from "processing bottleneck".

Usage:
    locust -f locustfile.py --host http://localhost:8080

The test video path is relative to CWD; run from the repo root.
"""

import json
import mimetypes
import os
import time

import websocket
from locust import between, events, task
from locust.contrib.fasthttp import FastHttpUser

TEST_FILE = os.getenv("LOAD_TEST_FILE", "model_eval/eval_data/clip_00.wav")
UPLOAD_PATH = "/api/v1/upload"
WS_PATH_TEMPLATE = "/api/v1/ws/{task_id}"

# How long to wait for a result before declaring the request failed.
# Real tasks (50+MB video) can take ~5 min; small clips take ~15 s, so allow generous headroom.
WS_RESULT_TIMEOUT_SEC = float(os.getenv("WS_RESULT_TIMEOUT_SEC", "600"))
RUN_JOBS = {}


@events.test_start.add_listener
def reset_jobs(environment, **kwargs):
    RUN_JOBS.clear()


@events.test_stop.add_listener
def export_jobs(environment, **kwargs):
    """Keep accepted jobs in the denominator even if their users were stopped."""
    output = os.getenv("LOAD_TEST_JOBS_JSON")
    if output:
        with open(output, "w", encoding="utf-8") as stream:
            json.dump({"snapshot_at": time.time(), "jobs": RUN_JOBS}, stream, indent=2)


def ws_url(host: str, task_id: str) -> str:
    scheme = "wss" if host.startswith("https") else "ws"
    base = host.replace("http://", "").replace("https://", "")
    return f"{scheme}://{base}{WS_PATH_TEMPLATE.format(task_id=task_id)}"

def _wait_for_result(ws: websocket.WebSocket, timeout: float, on_keepalive=None):
    """Ignore keepalives without extending the overall monotonic deadline."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Overall result deadline exceeded")
        ws.settimeout(remaining)
        data = ws.recv()
        if not data:
            raise websocket.WebSocketConnectionClosedException("Server disconnected")
        payload = json.loads(data)
        if payload.get("status") in {"completed", "error"}:
            return payload
        if payload.get("status") not in {"queued", "processing"}:
            raise ValueError(f"Unexpected task status: {payload.get('status')!r}")
        if on_keepalive:
            on_keepalive()


class VideoPipelineUser(FastHttpUser):
    wait_time = between(1, 3)

    @task
    def upload_and_track(self):
        if not os.path.exists(TEST_FILE):
            events.request.fire(
                request_type="SETUP",
                name="missing test video",
                response_time=0,
                response_length=0,
                exception=FileNotFoundError(TEST_FILE),
            )
            return

        upload_started_at = time.time()
        with open(TEST_FILE, "rb") as f:
            resp = self.client.post(
                UPLOAD_PATH,
                files={"file": (
                    os.path.basename(TEST_FILE), f,
                    mimetypes.guess_type(TEST_FILE)[0] or "application/octet-stream",
                )},
                name=f"POST {UPLOAD_PATH}",
            )

            if resp.status_code != 200:
                return

            task_id = resp.json().get("task_id")
            if not task_id:
                return

            RUN_JOBS[task_id] = {
                "upload_started_at": upload_started_at,
                "accepted_at": time.time(),
                "state": "pending",
                "keepalives": 0,
            }
            # time the processing result (WebSocket wait).
            self._track_result(task_id)

    def _track_result(self, task_id: str):
        start = time.perf_counter()
        ws = websocket.WebSocket()
        try:
            ws.connect(ws_url(self.host, task_id), timeout=WS_RESULT_TIMEOUT_SEC)
            remaining = WS_RESULT_TIMEOUT_SEC - (time.perf_counter() - start)

            def record_keepalive():
                job = RUN_JOBS[task_id]
                job["keepalives"] = job.get("keepalives", 0) + 1

            payload = _wait_for_result(ws, remaining, on_keepalive=record_keepalive)

            status = payload.get("status", "unknown")
            job = RUN_JOBS[task_id]
            job["observed_at"] = time.time()
            # The server's waiting timeout has no task_id and does not stop the task.
            terminal = payload.get("task_id") == task_id
            job["state"] = "completed" if status == "completed" else (
                "failed" if terminal else "pending"
            )
            job["observation"] = payload.get("error", payload.get("message", status))
            if status == "completed":
                events.request.fire(
                    request_type="WS",
                    name="result wait (completed)",
                    response_time=(time.perf_counter() - start) * 1000,
                    response_length=len(json.dumps(payload)),
                )
            else:
                events.request.fire(
                    request_type="WS",
                    name="result wait (error)",
                    response_time=(time.perf_counter() - start) * 1000,
                    response_length=len(json.dumps(payload)),
                    exception=RuntimeError(payload.get("error", payload.get("message", status))),
                )
        except (websocket.WebSocketException, TimeoutError, OSError, ValueError) as e:
            RUN_JOBS[task_id]["observation"] = str(e)
            events.request.fire(
                request_type="WS",
                name="result wait (observation failed)",
                response_time=(time.perf_counter() - start) * 1000,
                response_length=0,
                exception=e,
            )
        finally:
            ws.close()
