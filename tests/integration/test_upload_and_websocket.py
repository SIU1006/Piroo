import json
import time
from io import BytesIO
from threading import Thread

import pytest
from celery import Celery
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.integration


def test_real_broker_connection_failure_returns_503_and_removes_upload(
    isolated_upload_dir, monkeypatch
):
    unreachable = Celery("unreachable-boundary", broker="redis://127.0.0.1:1/0")
    unreachable.conf.update(
        task_publish_retry=False,
        broker_transport_options={"max_retries": 0, "socket_connect_timeout": 0.1},
    )

    @unreachable.task(name="unreachable_process_video")
    def unreachable_process_video(_task_id, _path):
        raise AssertionError("the task must never execute")

    monkeypatch.setattr("app.routes.upload.process_video", unreachable_process_video)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/upload",
                files={"file": ("boundary.mp3", BytesIO(b"media"), "audio/mpeg")},
            )
    finally:
        unreachable.close()

    assert response.status_code == 503
    assert response.json() == {"detail": "Task queue is unavailable"}
    assert list(isolated_upload_dir.iterdir()) == []


def test_websocket_keepalive_then_live_result(redis_client, monkeypatch):
    monkeypatch.setattr("app.routes.websocket.KEEPALIVE_INTERVAL_SEC", 0.05)
    monkeypatch.setattr("app.routes.websocket.RESULT_WAIT_TIMEOUT_SEC", 2)
    payload = {"status": "completed", "task_id": "live", "summary": "finished"}

    def publish_after_subscription():
        time.sleep(0.15)
        redis_client.publish("task:live", json.dumps(payload))

    publisher = Thread(target=publish_after_subscription)
    publisher.start()
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws/live") as websocket:
        assert websocket.receive_json() == {"status": "processing"}
        assert websocket.receive_json() == {"status": "processing"}
        assert websocket.receive_json() == payload
    publisher.join()


def test_websocket_timeout_sends_terminal_error(redis_client, monkeypatch):
    monkeypatch.setattr("app.routes.websocket.KEEPALIVE_INTERVAL_SEC", 0.02)
    monkeypatch.setattr("app.routes.websocket.RESULT_WAIT_TIMEOUT_SEC", 0.07)

    with TestClient(app) as client, client.websocket_connect("/api/v1/ws/timeout") as websocket:
        assert websocket.receive_json() == {"status": "processing"}
        terminal = None
        for _ in range(10):
            message = websocket.receive_json()
            if message.get("status") == "error":
                terminal = message
                break

    assert terminal == {"status": "error", "message": "Timed out waiting for the task to finish."}


def test_websocket_disconnect_removes_real_redis_subscription(redis_client, monkeypatch):
    monkeypatch.setattr("app.routes.websocket.KEEPALIVE_INTERVAL_SEC", 0.02)
    monkeypatch.setattr("app.routes.websocket.RESULT_WAIT_TIMEOUT_SEC", 2)

    with TestClient(app) as client, client.websocket_connect(
        "/api/v1/ws/disconnect"
    ) as websocket:
        assert websocket.receive_json() == {"status": "processing"}

    deadline = time.monotonic() + 1
    subscribers = 1
    while time.monotonic() < deadline:
        subscribers = redis_client.pubsub_numsub("task:disconnect")[0][1]
        if subscribers == 0:
            break
        time.sleep(0.02)
    assert subscribers == 0


def test_websocket_late_reconnections_receive_cached_result(redis_client):
    payload = {"status": "completed", "task_id": "late", "summary": "cached"}
    redis_client.setex("result:late", 60, json.dumps(payload))

    with TestClient(app) as client:
        for _ in range(2):
            with client.websocket_connect("/api/v1/ws/late") as websocket:
                assert websocket.receive_json() == payload
