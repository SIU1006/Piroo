"""Join a single-process Locust job ledger with Celery timing events.

Queue time is an interval because the unmodified API's enqueue timestamp is
between the client's upload start and acknowledgement. Clocks must be aligned.
Missing timings stay blank, and pending jobs never become zero-latency samples.
"""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def distribution(values):
    values = sorted(values)
    if not values:
        return {"samples": 0}
    return {
        "samples": len(values), "mean": statistics.mean(values),
        "p50": values[math.ceil(len(values) * 0.5) - 1],
        "p95": values[math.ceil(len(values) * 0.95) - 1], "max": values[-1],
    }


def summarize(run):
    ledger = json.loads((run / "jobs.json").read_text(encoding="utf-8-sig"))
    events = [json.loads(line) for line in
              (run / "events.jsonl").read_text(encoding="utf-8-sig").splitlines() if line]
    tasks = {}
    for event in events:
        if "uuid" not in event or event["timestamp"] > ledger["snapshot_at"]:
            continue
        task = tasks.setdefault(event["uuid"], {})
        if "task_id" in event:
            task["task_id"] = event["task_id"]
        if event["type"] == "task-started":
            task.setdefault("started_at", event["timestamp"])
        elif event["type"] in {"task-succeeded", "task-failed"}:
            task["finished_at"] = event["timestamp"]
            if "runtime" in event:
                task["runtime"] = event["runtime"]
    by_id = {task["task_id"]: task for task in tasks.values() if "task_id" in task}
    rows = []
    for task_id, job in ledger["jobs"].items():
        row = {"task_id": task_id, **job}
        row["upload_seconds"] = job["accepted_at"] - job["upload_started_at"]
        timing = by_id.get(task_id, {})
        # Some thread-pool start events are absent. Success runtime still records
        # execution duration, so infer the start from the terminal event clock.
        # That queue estimate also includes small event-dispatch overhead.
        if "started_at" not in timing and "runtime" in timing:
            timing["started_at"] = timing["finished_at"] - timing["runtime"]
            row["start_source"] = "inferred_from_success_runtime"
        elif "started_at" in timing:
            row["start_source"] = "task_started_event"
        if "started_at" in timing:
            started = timing["started_at"]
            row["queue_wait_lower_seconds"] = max(0, started - job["accepted_at"])
            row["queue_wait_upper_seconds"] = max(0, started - job["upload_started_at"])
            if "finished_at" in timing:
                row["processing_seconds"] = timing.get(
                    "runtime", timing["finished_at"] - started,
                )
        if job["state"] in {"completed", "failed"}:
            row["total_seconds"] = job["observed_at"] - job["upload_started_at"]
        rows.append(row)
    summary = {
        "snapshot_at": ledger["snapshot_at"], "accepted_uploads": len(rows),
        "keepalives": sum(row.get("keepalives", 0) for row in rows),
        "start_sources": {source: sum(row.get("start_source") == source for row in rows)
                          for source in ("task_started_event", "inferred_from_success_runtime")},
        **{state: sum(row["state"] == state for row in rows)
           for state in ("completed", "failed", "pending")},
        "seconds": {field: distribution([row[field] for row in rows if field in row])
                    for field in ("upload_seconds", "queue_wait_lower_seconds",
                                  "queue_wait_upper_seconds", "processing_seconds",
                                  "total_seconds")},
    }
    (run / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (run / "per_job.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted({key for row in rows for key in row}))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    summarize(parser.parse_args().run)
