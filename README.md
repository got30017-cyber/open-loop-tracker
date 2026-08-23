# Open Loop Tracker

Phase 1 provides the FastAPI application skeleton and the infrastructure-independent
case state machine. Case persistence and operational integrations are intentionally
deferred.

## Run locally

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The health check is available at `GET http://localhost:8000/health`.

Configuration can be overridden with `APP_NAME`, `APP_ENV`, and `DATABASE_URL`.
The default database URL is `sqlite:///./open_loop_tracker.db`; Phase 1 only
prepares the engine, session factory, and declarative base and does not create tables.

## Run tests

```powershell
cd backend
pytest
```

## Run with Docker

```powershell
docker compose up --build
```

The Compose setup runs only the API and uses a named volume for the future SQLite
database. It does not include Telegram, n8n, workers, or other later-phase services.
