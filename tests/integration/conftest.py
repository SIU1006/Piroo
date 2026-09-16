import os

import pytest
import redis

# A developer's gitignored .env must not leak into this disposable topology.
os.environ["REDIS_PASSWORD"] = os.getenv("INTEGRATION_REDIS_PASSWORD", "")


pytestmark = pytest.mark.integration


@pytest.fixture(scope="session", autouse=True)
def require_integration_services():
    if os.getenv("RUN_SERVICE_INTEGRATION") != "1":
        pytest.skip("set RUN_SERVICE_INTEGRATION=1 and start Redis and Whisper")


@pytest.fixture
def redis_client():
    client = redis.Redis.from_url(os.environ["BROKER_URL"], decode_responses=True)
    client.ping()
    client.flushdb()
    try:
        yield client
    finally:
        client.flushdb()
        client.close()
