import io
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from celery import Celery
from fastapi.testclient import TestClient

import task_store
import upload_storage
from app.main import app
from worker.tasks import TransientServiceError, process_video

pytestmark = pytest.mark.integration


@pytest.fixture
def object_bucket(monkeypatch):
    import os

    if not os.getenv("S3_ENDPOINT_URL"):
        pytest.fail("Object-storage boundary tests require an isolated S3_ENDPOINT_URL")
    monkeypatch.setenv("UPLOAD_STORAGE_BACKEND", "s3")
    bucket = "boundary-" + uuid4().hex
    monkeypatch.setenv("S3_UPLOAD_BUCKET", bucket)
    s3 = upload_storage.client()
    s3.create_bucket(Bucket=bucket)
    yield s3, bucket
    objects = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
    for obj in objects:
        s3.delete_object(Bucket=bucket, Key=obj["Key"])
    s3.delete_bucket(Bucket=bucket)


def test_upload_and_worker_use_independent_filesystems(
    redis_client, object_bucket, isolated_upload_dir, tmp_path, monkeypatch,
):
    source = tmp_path / "fixture.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(source)], check=True)
    worker_dir = tmp_path / "worker-private"
    worker_dir.mkdir()
    isolated_upload_dir = tmp_path / "producer-private"
    isolated_upload_dir.mkdir()
    monkeypatch.setattr("app.routes.upload.UPLOAD_DIR", isolated_upload_dir)
    monkeypatch.setattr("worker.tasks.UPLOAD_DIR", worker_dir)
    accepted = []
    monkeypatch.setattr(process_video, "apply_async", lambda **kwargs: accepted.append(kwargs))
    monkeypatch.setattr("worker.tasks.transcribe", lambda *_: "Boundary transcript")
    with TestClient(app) as api:
        response = api.post("/api/v1/upload", files={"file": ("clip.wav", source.read_bytes())})
        assert response.status_code == 200
        task_id = response.json()["task_id"]
        assert accepted[0]["task_id"] == task_id
        ref = accepted[0]["args"][1]
        assert ref.startswith("s3://")
        assert list(isolated_upload_dir.iterdir()) == []
        assert list(worker_dir.iterdir()) == []
        assert api.get(f"/api/v1/tasks/{task_id}").json()["status"] == "queued"

        def summarize(*_):
            assert api.get(f"/api/v1/tasks/{task_id}").json()["status"] == "running"
            return "Boundary summary"

        monkeypatch.setattr("worker.tasks.summarize", summarize)
        process_video.run.__wrapped__(task_id, ref)
        result = api.get(f"/api/v1/tasks/{task_id}").json()
        assert result["status"] == "completed" and result["summary"] == "Boundary summary"
    assert redis_client.zcard(task_store.OUTSTANDING_KEY) == 0
    assert list(worker_dir.iterdir()) == []
    s3, bucket = object_bucket
    assert s3.list_objects_v2(Bucket=bucket).get("Contents", []) == []


@pytest.mark.parametrize("recover", [True, False])
def test_retry_retains_object_but_cleans_worker_scratch(
    redis_client, object_bucket, tmp_path, monkeypatch, recover,
):
    task_id = str(uuid4())
    source = tmp_path / "source.mp3"
    source.write_bytes(b"test media")
    worker_dir = tmp_path / "worker"
    worker_dir.mkdir()
    monkeypatch.setattr("worker.tasks.UPLOAD_DIR", worker_dir)
    ref = upload_storage.reference(task_id, ".mp3", source)
    upload_storage.persist(source, ref)
    assert task_store.reserve(redis_client, task_id, ref)
    task_store.transition(redis_client, task_id, "queued", "waiting")
    monkeypatch.setattr("worker.tasks.ffmpeg.probe", lambda _: {"format": {"duration": "1"}})
    monkeypatch.setattr("worker.tasks.extract_audio", lambda _, path: Path(path).write_bytes(b"audio"))

    def unavailable(*_):
        raise TransientServiceError("temporary transcription outage")

    monkeypatch.setattr("worker.tasks.transcribe", unavailable)
    with pytest.raises(TransientServiceError):
        process_video.run.__wrapped__(task_id, ref)
    assert task_store.get(redis_client, task_id)["phase"] == "retrying"
    assert redis_client.zcard(task_store.OUTSTANDING_KEY) == 1
    assert list(worker_dir.iterdir()) == []
    s3, bucket = object_bucket
    s3.head_object(Bucket=bucket, Key=f"uploads/{task_id}.mp3")
    if recover:
        monkeypatch.setattr("worker.tasks.transcribe", lambda *_: "Recovered transcript")
        monkeypatch.setattr("worker.tasks.summarize", lambda *_: "Recovered summary")
        process_video.run.__wrapped__(task_id, ref)
    else:
        process_video.push_request(retries=process_video.max_retries)
        try:
            with pytest.raises(TransientServiceError):
                process_video.run.__wrapped__(task_id, ref)
        finally:
            process_video.pop_request()
    assert task_store.get(redis_client, task_id)["status"] == ("completed" if recover else "failed")
    assert redis_client.zcard(task_store.OUTSTANDING_KEY) == 0
    with pytest.raises(ClientError):
        s3.head_object(Bucket=bucket, Key=f"uploads/{task_id}.mp3")


def test_admission_is_atomic_across_producers_and_releases_on_terminal(redis_client, monkeypatch):
    monkeypatch.setattr(task_store, "MAX_OUTSTANDING_TASKS", 3)
    ids = [str(uuid4()) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = list(pool.map(lambda id_: task_store.reserve(redis_client, id_, "/tmp/test"), ids))
    assert sum(accepted) == 3
    completed = ids[accepted.index(True)]
    task_store.finish(redis_client, completed, {"status": "completed", "summary": "done"})
    assert task_store.reserve(redis_client, str(uuid4()), "/tmp/another")
    assert redis_client.zcard(task_store.OUTSTANDING_KEY) == 3
    # Terminal decisions are immutable, including late duplicate failures.
    assert not task_store.finish(redis_client, completed, {"status": "error", "error": "late"})
    assert task_store.get(redis_client, completed)["status"] == "completed"


def test_overdue_queued_task_fails_and_live_attempt_keeps_admission(redis_client):
    overdue, live = str(uuid4()), str(uuid4())
    for task_id in (overdue, live):
        assert task_store.reserve(redis_client, task_id, "/tmp/source")
        redis_client.zadd(task_store.OUTSTANDING_KEY, {task_id: time.time() - 1})
    redis_client.setex(f"active:{live}", 120, "running")
    assert list(task_store.expire_overdue(redis_client)) == [(overdue, "/tmp/source")]
    assert task_store.get(redis_client, overdue)["status"] == "failed"
    assert not task_store.transition(redis_client, overdue, "running", "processing")
    assert redis_client.zscore(task_store.OUTSTANDING_KEY, live) > time.time()


def test_full_admission_rejects_before_writing_or_enqueuing(
    redis_client, isolated_upload_dir, monkeypatch,
):
    monkeypatch.setattr(task_store, "MAX_OUTSTANDING_TASKS", 1)
    assert task_store.reserve(redis_client, str(uuid4()), "/tmp/existing")
    monkeypatch.setattr(process_video, "apply_async", lambda **_: pytest.fail("must not enqueue"))
    with TestClient(app) as api:
        response = api.post("/api/v1/upload", files={"file": ("test.mp3", io.BytesIO(b"media"))})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert list(isolated_upload_dir.iterdir()) == []


def test_task_endpoint_reports_failed_unknown_and_invalid_ids(redis_client):
    task_id = str(uuid4())
    task_store.reserve(redis_client, task_id, "/tmp/source")
    task_store.finish(redis_client, task_id, {"status": "error", "task_id": task_id, "error": "bad media"})
    with TestClient(app) as api:
        result = api.get(f"/api/v1/tasks/{task_id}")
        assert result.status_code == 200 and result.json()["status"] == "failed"
        assert api.get(f"/api/v1/tasks/{uuid4()}").status_code == 404
        assert api.get("/api/v1/tasks/not-a-uuid").status_code == 422


def test_real_broker_failure_cleans_object_and_admission(
    redis_client, object_bucket, isolated_upload_dir, monkeypatch,
):
    unreachable = Celery("unreachable-object-boundary", broker="redis://127.0.0.1:1/0")
    unreachable.conf.update(task_publish_retry=False,
                            broker_transport_options={"max_retries": 0, "socket_connect_timeout": 0.1})

    @unreachable.task(name="unreachable_object_boundary")
    def task(*_):
        pytest.fail("must not execute")

    monkeypatch.setattr("app.routes.upload.process_video", task)
    try:
        with TestClient(app) as api:
            result = api.post("/api/v1/upload", files={"file": ("clip.mp3", b"media")})
    finally:
        unreachable.close()
    assert result.status_code == 503
    s3, bucket = object_bucket
    assert s3.list_objects_v2(Bucket=bucket).get("Contents", []) == []
    assert redis_client.zcard(task_store.OUTSTANDING_KEY) == 0
    assert list(isolated_upload_dir.iterdir()) == []


def test_readiness_requires_accessible_upload_bucket(redis_client, object_bucket, monkeypatch):
    with TestClient(app) as api:
        assert api.get("/readyz").status_code == 200
        monkeypatch.setattr(upload_storage, "client", lambda **_: (_ for _ in ()).throw(ConnectionError()))
        assert api.get("/readyz").status_code == 503
        assert api.get("/healthz").status_code == 200


def test_late_delivery_cannot_restart_cancelled_admission(redis_client, monkeypatch):
    task_id = str(uuid4())
    ref = f"s3://cancelled-test/uploads/{task_id}.mp3"
    assert task_store.reserve(redis_client, task_id, ref)
    task_store.cancel(redis_client, task_id)
    monkeypatch.setattr(upload_storage, "materialize", lambda *_: pytest.fail("must not fetch"))
    process_video.run.__wrapped__(task_id, ref)
    assert redis_client.zcard(task_store.OUTSTANDING_KEY) == 0
    assert task_store.get(redis_client, task_id) is None


def test_transition_cannot_recreate_missing_reservation(redis_client):
    task_id = str(uuid4())
    assert task_store.reserve(redis_client, task_id, "/tmp/source")
    redis_client.delete(f"status:{task_id}")
    before = redis_client.zscore(task_store.OUTSTANDING_KEY, task_id)
    assert not task_store.transition(redis_client, task_id, "queued", "waiting")
    assert not redis_client.exists(f"status:{task_id}")
    assert redis_client.zscore(task_store.OUTSTANDING_KEY, task_id) == before
    active_id = str(uuid4())
    assert task_store.reserve(redis_client, active_id, "/tmp/active")
    assert task_store.transition(redis_client, active_id, "running", "processing")
    assert task_store.get(redis_client, active_id)["status"] == "running"
