# ---- Stage 1: builder ----
FROM python:3.12-slim AS builder
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1

COPY requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt

# ---- Stage 2: runtime ----
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=builder /install /usr/local
COPY . .
EXPOSE 8003

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health')" || exit 1

# alembic upgrade head runs before uvicorn so the mock bank's own tables
# (mock_customers/mock_accounts/mock_transactions) exist before the app
# serves traffic — mirrors the sibling ai-service's prod compose entry.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8003"]
