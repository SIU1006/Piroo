import json
from unittest.mock import MagicMock, patch

import pytest

import task_store
from worker.tasks import (
    cleanup,
    get_originalpath,
    publish_result,
    store_failure,
    store_success,
    validate_duration,
)

'''
Direct unit tests for the small, narrowly-scoped helpers in tasks.py.
'''


# ---------------------------------------------------------------------------
# validate_duration
# ---------------------------------------------------------------------------

def test_validate_duration_ok_under_limit():
    validate_duration({"format": {"duration": "120"}})  # 2 minutes, should not raise


def test_validate_duration_raises_over_limit():
    with pytest.raises(ValueError, match="Audio file duration exceeds 30 minutes limit: 50.00 minutes"):
        validate_duration({"format": {"duration": str(50 * 60)}})


def test_validate_duration_exactly_at_limit_is_ok():
    # Boundary check: exactly 30 minutes should not raise (only strictly over does)
    validate_duration({"format": {"duration": str(30 * 60)}})


def test_validate_duration_custom_limit():
    with pytest.raises(ValueError, match="exceeds 1 minutes limit"):
        validate_duration({"format": {"duration": "120"}}, max_minutes=1)


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

def test_cleanup_removes_existing_paths(tmp_path):
    f1 = tmp_path / "a.mp4"
    f2 = tmp_path / "b.mp3"
    f1.write_bytes(b"x")
    f2.write_bytes(b"y")

    cleanup(str(f1), str(f2))

    assert not f1.exists()
    assert not f2.exists()


def test_cleanup_ignores_missing_paths(tmp_path):
    missing = tmp_path / "does-not-exist.mp3"
    cleanup(str(missing))  # should not raise


def test_cleanup_with_no_paths_does_nothing():
    cleanup()  # should not raise with zero args


# ---------------------------------------------------------------------------
# get_originalpath
# ---------------------------------------------------------------------------

def test_get_originalpath_returns_path_when_present():
    r = MagicMock()
    r.get.return_value = b"running:/tmp/uploads/input.mp4"

    result = get_originalpath(r, "heartbeat:task-1")

    assert result == "/tmp/uploads/input.mp4"
    r.get.assert_called_once_with("heartbeat:task-1")


def test_get_originalpath_returns_none_when_heartbeat_missing():
    r = MagicMock()
    r.get.return_value = None

    assert get_originalpath(r, "heartbeat:task-1") is None


def test_get_originalpath_returns_none_when_no_path_segment():
    # heartbeat value with nothing after the colon
    r = MagicMock()
    r.get.return_value = b"running:"

    assert get_originalpath(r, "heartbeat:task-1") is None


# ---------------------------------------------------------------------------
# publish_result
# ---------------------------------------------------------------------------

def test_publish_result_uses_atomic_terminal_transition():
    r = MagicMock()
    payload = {"status": "completed", "task_id": "task-1", "summary": "hi"}

    publish_result(r, "task-1", payload)

    expected_message = json.dumps(payload)
    args = r.eval.call_args.args
    assert args[:5] == (task_store.FINISH_SCRIPT, 3, "admission:tasks", "status:task-1", "result:task-1")
    assert args[5:9] == ("task-1", "completed", expected_message, 3600)
    assert args[-1] == "task:task-1"


# ---------------------------------------------------------------------------
# store_success / store_failure
#
# These are tested by mocking publish_result itself rather than a bare redis mock — that isolates
# "does store_success build the right payload" from "does publish_result
# talk to redis correctly", which is already covered above.
# ---------------------------------------------------------------------------

def test_store_success_builds_completed_payload():
    r = MagicMock()
    with patch("worker.tasks.publish_result") as mock_publish:
        store_success(r, "task-1", "a short summary")

    mock_publish.assert_called_once_with(r, "task-1", {
        "status": "completed",
        "task_id": "task-1",
        "summary": "a short summary",
    })


def test_store_failure_builds_error_payload_with_stringified_exception():
    r = MagicMock()
    with patch("worker.tasks.publish_result") as mock_publish:
        store_failure(r, "task-1", ValueError("boom"))

    mock_publish.assert_called_once_with(r, "task-1", {
        "status": "error",
        "task_id": "task-1",
        "error": "boom",
    })
