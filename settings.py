import os
from pathlib import Path

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
EVAL_DIR = Path(__file__).parent / "eval_data"
MANIFEST_PATH = EVAL_DIR / "manifest.json"


REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")


def with_password(url: str, password: str | None) -> str:
    """Inject a Redis password into a `redis://` URL when one is set and absent."""
    if not password or "@" in url:
        return url
    scheme, _, rest = url.partition("://")
    return f"{scheme}://default:{password}@{rest}"


BROKER_URL = with_password(
    os.getenv("BROKER_URL", "redis://localhost:6379/0"), REDIS_PASSWORD
)
WHISPER_URL = os.getenv("WHISPER_URL", "http://localhost:3000")
LLM_MODEL = os.getenv("LLM_MODEL")