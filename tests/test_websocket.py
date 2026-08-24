import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_cache_hit(monkeypatch):
    """Test if result is immediately returned without blocking on listen()"""

    with patch("app.routes.websocket.redis") as mock_redis_module, \
         patch("app.routes.websocket.wait_for_message") as mock_wait:
        mock_redis_instance = MagicMock()
        mock_redis_module.Redis.from_url.return_value = mock_redis_instance

        cached_result = json.dumps({
            "status": "completed",
            "task_id": "cached-task",
            "summary": "already done"
        })
        mock_redis_instance.get.return_value = cached_result.encode("utf-8") # Redis return by bytes

        mock_pubsub = MagicMock()
        mock_redis_instance.pubsub.return_value = mock_pubsub

        with client.websocket_connect("/api/v1/ws/cached-task") as websocket: # Connect
            data = websocket.receive_text()

        mock_pubsub.subscribe.assert_called_once_with("task:cached-task")

        mock_redis_instance.get.assert_called_once_with("result:cached-task") # See if cached is checked

        mock_wait.assert_not_called() # wait_for_message was never called

        assert json.loads(data) == json.loads(cached_result) # Verify cached data

def test_cache_miss(monkeypatch):
    """Otherwise, wait for result with pubsub"""

    with patch("app.routes.websocket.redis") as mock_redis_module, \
        patch("app.routes.websocket.wait_for_message") as mock_wait:
        mock_redis_instance = MagicMock()
        mock_redis_module.Redis.from_url.return_value = mock_redis_instance

        live_result = json.dumps({
            "status": "completed",
            "task_id": "live-task",
            "summary": "already done"
        })

        mock_redis_instance.get.return_value = None # No Cache

        mock_pubsub = MagicMock()
        mock_redis_instance.pubsub.return_value = mock_pubsub

        mock_wait.return_value = live_result.encode("utf-8")

        with client.websocket_connect("/api/v1/ws/live-task") as websocket: # Connect
            data = websocket.receive_text()

        mock_pubsub.subscribe.assert_called_once_with("task:live-task") # Make sure subscribe happens first
        mock_redis_instance.get.assert_called_once_with("result:live-task") # Check cache
        mock_wait.assert_called_once_with(mock_pubsub)

        assert json.loads(data) == json.loads(live_result) # Verify cached data