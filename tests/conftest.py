import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("BROKER_URL", "redis://redis-service:6379/0")
os.environ.setdefault("WHISPER_URL", "http://whisper-service:3000")
os.environ.setdefault("OLLAMA_HOST", "http://ollama-service:11434")
os.environ.setdefault("LLM_MODEL", "llama3.2")
os.environ.setdefault("UPLOAD_STORAGE_BACKEND", "local")
os.environ.setdefault("LOCUST_SKIP_MONKEY_PATCH", "1")

session_upload_dir = tempfile.mkdtemp(prefix="asyncvtp-test-uploads")
os.environ.setdefault("UPLOAD_DIR", session_upload_dir)


@pytest.fixture(autouse=True)
def isolated_unit_admission(request, monkeypatch):
    if "integration" in request.node.keywords:
        return
    from app.routes import upload

    store = SimpleNamespace(client=MagicMock(), reserve=MagicMock(return_value=True),
                            transition=MagicMock(return_value=True), cancel=MagicMock(),
                            UPLOAD_TIMEOUT_SECONDS=120)
    monkeypatch.setattr(upload, "task_store", store)

@pytest.fixture
def isolated_upload_dir(tmp_path, monkeypatch): # tmp_path: per-test temp dir from pytest
    """
    Redirects app.routes.upload.UPLOAD_DIR to a per-test tmp_path so uploaded
    files never touch the real uploads/ folder and never leak between tests.
    """
    from app.routes import upload as upload_module

    monkeypatch.setattr(upload_module, "UPLOAD_DIR", tmp_path) # temp set UPLOAD_DIR = tmp_path
    return tmp_path
