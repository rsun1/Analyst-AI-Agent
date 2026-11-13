# HTTP API service (FastAPI). Build context is the repo root (hosting platforms build from there). The Streamlit UI has its own image: Dockerfile.ui.
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY ["AI Agent/requirements-api.txt", "./"]
RUN pip install --no-cache-dir -r requirements-api.txt

COPY ["AI Agent/", "./"]

# Run as a non-root user.
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8080')+'/health').status==200 else 1)"

# Config (PG_*/PG_*_DSN, OPENAI_API_KEY, API_KEY, CORS_ORIGINS) comes from environment variables, never the image.
# Honors $PORT (set by the host at runtime); single process to stay inside 256 MB.
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8080}"]
