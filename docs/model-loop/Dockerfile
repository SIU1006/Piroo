FROM python:3.11-slim
WORKDIR /workspace
COPY requirements.txt requirements-load-test.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-load-test.txt \
    mlflow==3.15.0 faster-whisper==1.2.1 ruff==0.16.3
ENTRYPOINT ["python"]
