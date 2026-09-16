FROM python:3.11-slim
WORKDIR /workspace
COPY requirements-load-test.txt .
RUN pip install --no-cache-dir -r requirements-load-test.txt
ENTRYPOINT ["locust"]
