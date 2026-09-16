"""Redis task states and atomic admission for queued, running and retrying jobs."""
import json
import os
import time

import redis

from settings import BROKER_URL

OUTSTANDING_KEY = "admission:tasks"
RESULT_TTL_SECONDS = 3600
STATE_TTL_SECONDS = 4 * 3600
MAX_OUTSTANDING_TASKS = int(os.getenv("MAX_OUTSTANDING_TASKS", "100"))
QUEUE_TIMEOUT_SECONDS = int(os.getenv("TASK_QUEUE_TIMEOUT_SECONDS", "600"))
UPLOAD_TIMEOUT_SECONDS = int(os.getenv("UPLOAD_TIMEOUT_SECONDS", "120"))
if min(MAX_OUTSTANDING_TASKS, QUEUE_TIMEOUT_SECONDS, UPLOAD_TIMEOUT_SECONDS) < 1:
    raise ValueError("Admission and timeout limits must be positive")
if max(QUEUE_TIMEOUT_SECONDS, UPLOAD_TIMEOUT_SECONDS + 30) >= STATE_TTL_SECONDS:
    raise ValueError("Queue and upload deadlines must be shorter than task-state retention")

RESERVE_SCRIPT = """
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then return 0 end
redis.call('HSET', KEYS[2], 'status', 'queued', 'phase', 'uploading',
    'object_ref', ARGV[3], 'created_at', ARGV[4], 'updated_at', ARGV[4])
redis.call('EXPIRE', KEYS[2], ARGV[6])
redis.call('ZADD', KEYS[1], ARGV[5], ARGV[2])
return 1
"""

TRANSITION_SCRIPT = """
local state = redis.call('HGET', KEYS[2], 'status')
if state == 'completed' or state == 'failed' then return 0 end
redis.call('HSET', KEYS[2], 'status', ARGV[2], 'phase', ARGV[3], 'updated_at', ARGV[4])
redis.call('HSETNX', KEYS[2], 'created_at', ARGV[4])
redis.call('EXPIRE', KEYS[2], ARGV[6])
redis.call('ZADD', KEYS[1], ARGV[5], ARGV[1])
return 1
"""

FINISH_SCRIPT = """
if redis.call('EXISTS', KEYS[3]) == 1 then return 0 end
redis.call('SET', KEYS[3], ARGV[3], 'EX', ARGV[4])
redis.call('HSET', KEYS[2], 'status', ARGV[2], 'phase', 'finished', 'updated_at', ARGV[5])
redis.call('EXPIRE', KEYS[2], ARGV[4])
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('PUBLISH', ARGV[6], ARGV[3])
return 1
"""

EXPIRE_SCRIPT = """
local deadline = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not deadline or tonumber(deadline) > tonumber(ARGV[2]) then return false end
if redis.call('EXISTS', KEYS[4]) == 1 then
    redis.call('ZADD', KEYS[1], tonumber(ARGV[2]) + 60, ARGV[1])
    return false
end
local ref = redis.call('HGET', KEYS[2], 'object_ref')
if redis.call('EXISTS', KEYS[3]) == 0 then
    redis.call('SET', KEYS[3], ARGV[3], 'EX', ARGV[4])
    redis.call('HSET', KEYS[2], 'status', 'failed', 'phase', 'finished', 'updated_at', ARGV[2])
    redis.call('EXPIRE', KEYS[2], ARGV[4])
    redis.call('PUBLISH', ARGV[5], ARGV[3])
end
redis.call('ZREM', KEYS[1], ARGV[1])
return ref
"""


def client():
    return redis.Redis.from_url(BROKER_URL, socket_connect_timeout=3, socket_timeout=5)


def keys(task_id):
    return OUTSTANDING_KEY, f"status:{task_id}", f"result:{task_id}"


def reserve(r, task_id, ref):
    now = time.time()
    return bool(r.eval(RESERVE_SCRIPT, 2, *keys(task_id)[:2], MAX_OUTSTANDING_TASKS,
                       task_id, ref, now, now + UPLOAD_TIMEOUT_SECONDS + 30, STATE_TTL_SECONDS))


def transition(r, task_id, status, phase, timeout=QUEUE_TIMEOUT_SECONDS):
    now = time.time()
    return bool(r.eval(TRANSITION_SCRIPT, 2, *keys(task_id)[:2], task_id, status, phase,
                       now, now + timeout, STATE_TTL_SECONDS))


def cancel(r, task_id):
    with r.pipeline(transaction=True) as pipe:
        pipe.delete(f"status:{task_id}")
        pipe.zrem(OUTSTANDING_KEY, task_id)
        pipe.execute()


def finish(r, task_id, payload):
    status = "completed" if payload["status"] == "completed" else "failed"
    return bool(r.eval(FINISH_SCRIPT, 3, *keys(task_id), task_id, status,
                       json.dumps(payload), RESULT_TTL_SECONDS, time.time(), f"task:{task_id}"))


def get(r, task_id):
    with r.pipeline(transaction=True) as pipe:
        pipe.get(f"result:{task_id}")
        pipe.hgetall(f"status:{task_id}")
        result, record = pipe.execute()
    if result:
        payload = json.loads(result)
        return {**payload, "status": "completed" if payload["status"] == "completed" else "failed"}
    if not record:
        return None
    record = {(key.decode() if isinstance(key, bytes) else key):
              (value.decode() if isinstance(value, bytes) else value) for key, value in record.items()}
    return {"task_id": task_id, "status": record["status"], "phase": record["phase"],
            "created_at": float(record["created_at"]), "updated_at": float(record["updated_at"])}


def expire_overdue(r):
    now = time.time()
    for raw_id in r.zrangebyscore(OUTSTANDING_KEY, 0, now, start=0, num=100):
        task_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
        payload = {"status": "error", "task_id": task_id,
                   "error": "Task exceeded its queue or processing deadline."}
        ref = r.eval(EXPIRE_SCRIPT, 4, *keys(task_id), f"active:{task_id}", task_id,
                     now, json.dumps(payload), RESULT_TTL_SECONDS, f"task:{task_id}")
        if ref:
            yield task_id, ref.decode() if isinstance(ref, bytes) else ref
