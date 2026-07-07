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

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
