"""Load test for the AsyncVTP pipeline.

Exercises the *full* path a real browser takes, not just the upload ack:

  1. POST /api/v1/upload          -> queued ack (~ms) with a task_id
  2. WS  /api/v1/ws/{task_id}     -> wait for the completed/error result

Both stages are timed separately so the report can distinguish
"ingestion bottleneck" from "processing bottleneck".

Usage:
    locust -f locustfile.py --host http://localhost:8080

The test video path is relative to CWD; run from the repo root.
"""

import json
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


def ws_url(host: str, task_id: str) -> str:
    scheme = "wss" if host.startswith("https") else "ws"
    base = host.replace("http://", "").replace("https://", "")
    return f"{scheme}://{base}{WS_PATH_TEMPLATE.format(task_id=task_id)}"

def _wait_for_result(ws: websocket.WebSocket, timeout: float):
    """Block (gevent-cooperatively) until the server pushes one message."""
    ws.settimeout(timeout)
    data = ws.recv()
    return json.loads(data)

def _wait_for_result(ws: websocket.WebSocket, timeout: float):
    """Block (gevent-cooperatively) until the server pushes one message."""
    ws.settimeout(timeout)
    data = ws.recv()
    return json.loads(data)


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

        with open(TEST_FILE, "rb") as f:
            resp = self.client.post(
                UPLOAD_PATH,
                files={"file": (os.path.basename(TEST_FILE), f, "video/mp4")},
                name=f"POST {UPLOAD_PATH}",
            )

            if resp.status_code != 200:
                return

            task_id = resp.json().get("task_id")
            if not task_id:
                return

            # time the processing result (WebSocket wait).
            self._track_result(task_id)

    def _track_result(self, task_id: str):
        start = time.perf_counter()
        ws = websocket.WebSocket()
        try:
            ws.connect(ws_url(self.host, task_id))
            payload = _wait_for_result(ws, WS_RESULT_TIMEOUT_SEC)

            status = payload.get("status", "unknown")
            if status == "completed":
                events.request.fire(
                    request_type="WS",
                    name="end-to-end result (completed)",
                    response_time=(time.perf_counter() - start) * 1000,
                    response_length=len(json.dumps(payload)),
                )
            else:
                events.request.fire(
                    request_type="WS",
                    name="end-to-end result (error)",
                    response_time=(time.perf_counter() - start) * 1000,
                    response_length=len(json.dumps(payload)),
                    exception=RuntimeError(payload.get("error", "unknown error")),
                )
        except (websocket.WebSocketException, TimeoutError, OSError) as e:
            events.request.fire(
                request_type="WS",
                name="end-to-end result",
                response_time=(time.perf_counter() - start) * 1000,
                response_length=0,
                exception=e,
            )
        finally:
            ws.close()
