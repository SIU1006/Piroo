import json

import pytest
import websocket

from locustfile import RUN_JOBS, VideoPipelineUser, _wait_for_result


class Socket:
    def __init__(self, messages, clock):
        self.messages = iter(messages)
        self.clock = clock
        self.timeouts = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def recv(self):
        self.clock[0] += 1
        return next(self.messages)


@pytest.mark.parametrize("status", ["completed", "error"])
def test_keepalives_wait_for_terminal_status(monkeypatch, status):
    clock = [0]
    monkeypatch.setattr("locustfile.time.monotonic", lambda: clock[0])
    ws = Socket([
        '{"status":"processing"}', '{"status":"processing"}',
        json.dumps({"status": status}),
    ], clock)
    assert _wait_for_result(ws, 10)["status"] == status
    assert ws.timeouts == [10, 9, 8]


def test_keepalives_do_not_extend_overall_deadline(monkeypatch):
    clock = [0]
    monkeypatch.setattr("locustfile.time.monotonic", lambda: clock[0])
    ws = Socket(['{"status":"processing"}'] * 3, clock)
    with pytest.raises(TimeoutError, match="Overall"):
        _wait_for_result(ws, 3)
    assert ws.timeouts == [3, 2, 1]


def test_disconnect_is_an_observation_failure(monkeypatch):
    clock = [0]
    monkeypatch.setattr("locustfile.time.monotonic", lambda: clock[0])
    with pytest.raises(websocket.WebSocketConnectionClosedException):
        _wait_for_result(Socket([""], clock), 10)


def test_server_wait_timeout_leaves_accepted_job_pending(monkeypatch):
    class TimeoutSocket:
        def connect(self, *args, **kwargs):
            pass

        def settimeout(self, timeout):
            pass

        def recv(self):
            return '{"status":"error","message":"Timed out waiting for task"}'

        def close(self):
            pass

    user = VideoPipelineUser.__new__(VideoPipelineUser)
    user.host = "http://example.test"
    monkeypatch.setattr("locustfile.websocket.WebSocket", TimeoutSocket)
    monkeypatch.setattr("locustfile.events.request.fire", lambda **kwargs: None)
    monkeypatch.setitem(RUN_JOBS, "waiting", {"state": "pending"})
    user._track_result("waiting")
    assert RUN_JOBS["waiting"]["state"] == "pending"
