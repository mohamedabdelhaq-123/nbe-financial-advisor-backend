from fastapi import FastAPI, Response, status

from app.db import check_db_connection
from app.routes_accounts import router as accounts_router
from app.routes_internal import router as internal_router
from app.routes_simulate import router as simulate_router

app = FastAPI(title="mock-bank-sync", version="0.1.0")

app.include_router(internal_router)
app.include_router(accounts_router)
app.include_router(simulate_router)


@app.get("/health")
def health(response: Response):
    """Unlike mock-bank-oauth, this service owns data — so /health actually
    checks Postgres connectivity, not just that the process is up."""
    if check_db_connection():
        return {"status": "ok"}

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "unavailable", "detail": "database unreachable"}
