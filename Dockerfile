FROM python:3.11-slim

# Install ffmpeg at OS level
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .
# add a non-root user
RUN useradd --create-home appuser 

RUN mkdir -p /app/uploads

# Makes appuser owner of /app - non-root user could not write to /app/uploads without this
RUN chown -R appuser:appuser /app
# Run as appuser
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]