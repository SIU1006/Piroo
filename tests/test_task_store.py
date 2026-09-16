import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("limit,valid", [(0, False), (1, True), (100, True), (101, False)])
def test_admission_limit_validation(limit, valid):
    env = {**os.environ, "MAX_OUTSTANDING_TASKS": str(limit),
           "TASK_QUEUE_TIMEOUT_SECONDS": "600", "UPLOAD_TIMEOUT_SECONDS": "120"}
    result = subprocess.run([sys.executable, "-c", "import task_store"],
                            env=env, capture_output=True, text=True, check=False)
    assert (result.returncode == 0) == valid, result.stderr
    if limit > 100:
        assert "must not exceed 100" in result.stderr
