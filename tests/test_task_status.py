from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

from app.main import app


def test_status_unavailable_returns_503_and_closes_client():
    redis = MagicMock()
    redis.pipeline.return_value.__enter__.return_value.execute.side_effect = ConnectionError("offline")
    with patch("app.routes.tasks.task_store.client", return_value=redis), TestClient(app) as api:
        result = api.get(f"/api/v1/tasks/{uuid4()}")
    assert result.status_code == 503
    redis.close.assert_called_once()


def test_invalid_status_id_is_rejected_without_redis_access():
    with patch("app.routes.tasks.task_store.client") as redis, TestClient(app) as api:
        result = api.get("/api/v1/tasks/not-an-id")
    assert result.status_code == 422
    redis.assert_not_called()
