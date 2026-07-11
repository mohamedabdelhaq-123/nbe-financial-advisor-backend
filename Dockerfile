# ---- Stage 1: builder ----
FROM python:3.12-slim AS builder
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1

ARG BUILD_ENV=production
COPY requirements.txt requirements-dev.txt ./

RUN if [ "$BUILD_ENV" = "development" ]; then \
        pip install --prefix=/install -r requirements-dev.txt; \
    else \
        pip install --prefix=/install -r requirements.txt; \
    fi

# ---- Stage 2: runtime ----
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=builder /install /usr/local
COPY . .
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/')" || exit 1

# --worker-class gthread --timeout 0: the single multiplexed SSE connection
# (core/views/events.py's GET /events/stream) is long-lived and would
# otherwise occupy an entire sync worker for its lifetime and get SIGKILL'd
# by gunicorn's default 30s request timeout. gthread lets one worker serve
# many connections that spend their time blocked on the Redis socket read,
# not CPU.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--worker-class", "gthread", "--workers", "2", "--threads", "4", "--timeout", "0"]
