# Backend stub (M1)

A throwaway Django + Postgres stub to validate the Docker/Compose setup.
Replace `core/` with the real backend later — the Dockerfile and compose stay the same.

## Run

```bash
docker compose up --build
```

Then open:
- http://localhost:8000/health/  -> {"status": "ok"}   (app is up)
- http://localhost:8000/db/      -> {"db": "ok", "ping_count": N}  (DB reachable)

## The M1 test: does data survive a restart?

```bash
# write three rows
curl -X POST http://localhost:8000/ping/
curl -X POST http://localhost:8000/ping/
curl -X POST http://localhost:8000/ping/

# see the count
curl http://localhost:8000/db/        # ping_count: 3

# restart everything
docker compose down
docker compose up

# count is still 3  -> the named volume works
curl http://localhost:8000/db/        # ping_count: 3
```

If you run `docker compose down -v` (note the `-v`), the volume is deleted and the
count resets to 0 — that's the difference a named volume makes.

## Endpoints
| Path       | Method | Purpose                          |
|------------|--------|----------------------------------|
| `/health/` | GET    | Is the app alive? (no DB)        |
| `/db/`     | GET    | Can it reach Postgres? + count   |
| `/ping/`   | POST   | Write one row                    |
