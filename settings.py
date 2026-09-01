import os
from pathlib import Path

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
EVAL_DIR = Path(__file__).parent / "eval_data"
MANIFEST_PATH = EVAL_DIR / "manifest.json"


BROKER_URL = os.getenv("BROKER_URL", "redis://localhost:6379/0")
WHISPER_URL = os.getenv("WHISPER_URL", "http://localhost:3000")
LLM_MODEL = os.getenv("LLM_MODEL")