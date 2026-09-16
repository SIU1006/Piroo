"""Run inside an application pod; print only task timing events as JSON lines.

python capture_events.py --seconds 180 > events.jsonl
Workers must have task events enabled for the observation window.
"""
import argparse
import ast
import json
import time

from worker.celery_app import celery_app


def capture(seconds):
    deadline = time.monotonic() + seconds

    def record(event):
        if event["type"] not in {
            "task-received", "task-started", "task-succeeded", "task-failed", "task-retried",
        }:
            return
        output = {key: event[key] for key in (
            "type", "uuid", "timestamp", "runtime", "hostname", "name",
        ) if key in event}
        if event["type"] == "task-received" and event.get("name") == "process_video":
            output["task_id"] = ast.literal_eval(event["args"])[0]
        print(json.dumps(output), flush=True)

    with celery_app.connection() as connection:
        receiver = celery_app.events.Receiver(connection, handlers={"*": record})
        with receiver.consumer_context() as (event_connection, _, _):
            print(json.dumps({"type": "capture-ready", "timestamp": time.time()}), flush=True)
            while time.monotonic() < deadline:
                try:
                    event_connection.drain_events(timeout=min(1, deadline - time.monotonic()))
                except TimeoutError:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=180)
    capture(parser.parse_args().seconds)
