from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_compose_services_share_required_runtime_configuration():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    for name in ("fastapi", "celery", "celery-beat"):
        environment = services[name]["environment"]
        assert environment["LLM_MODEL"]
        assert environment["REDIS_PASSWORD"]

    assert services["redis"]["environment"]["REDIS_PASSWORD"]
    assert "$$REDIS_PASSWORD" in services["redis"]["healthcheck"]["test"][-1]
    assert "--schedule=/tmp/celerybeat-schedule" in services["celery-beat"]["command"]
    assert services["fastapi"]["depends_on"]["ollama-model"]["condition"] == "service_completed_successfully"
    assert services["celery"]["depends_on"]["whisper-service"]["condition"] == "service_healthy"


def test_local_secrets_are_outside_the_docker_build_context():
    patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "secrets/" in patterns


def test_secret_setup_is_bash_and_shell_files_keep_lf_endings():
    script = (ROOT / "k8s" / "setup-secrets.sh").read_text(encoding="utf-8")
    assert script.startswith("#!/usr/bin/env bash\n")
    assert "param(" not in script
    assert "--set-file" in script
    assert "*.sh text eol=lf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")
