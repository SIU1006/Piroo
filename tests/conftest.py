import os

os.environ.setdefault("BROKER_URL", "redis://redis-service:6379/0")
os.environ.setdefault("BACKEND_URL", "redis://redis-service:6379/0")
os.environ.setdefault("WHISPER_URL", "http://whisper-service:3000")
os.environ.setdefault("OLLAMA_HOST", "http://ollama-service:11434")
os.environ.setdefault("LLM_MODEL", "llama3.2")