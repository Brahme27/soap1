# ---- 1. base ----
FROM ghcr.io/astral-sh/uv:python3.12-alpine
RUN adduser -D -u 1000 appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_PROGRESS=1 \
    UV_LINK_MODE=copy \
    PORT=8000 \
    GUNICORN_CMD_ARGS="--timeout 60 --worker-class uvicorn.workers.UvicornWorker --workers 2 --keep-alive 65 --max-requests 4000 --max-requests-jitter 500 --graceful-timeout 30 --backlog 4096 --access-logfile - --log-level info --pid /tmp/gunicorn.pid --bind 0.0.0.0:8000"

WORKDIR /app

# ---- 2. cache layer: install deps ----
COPY soap_note_generator/pyproject.toml ./
COPY soap_note_generator/uv.lock ./
RUN uv sync --frozen

# ---- 3. copy source ----
COPY soap_note_generator/ ./

RUN chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:${PORT}/ >/dev/null || exit 1
CMD ["uv", "run", "streamlit", "run", "app.py", "--server.port=8000", "--server.address=0.0.0.0"]