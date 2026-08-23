# Open Loop Tracker

Phase 2 provides the FastAPI application skeleton, infrastructure-independent case
state machine, SQLite persistence records, and an injected-session application
service. Operational integrations and business REST endpoints remain deferred.

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
The default database URL is `sqlite:///./open_loop_tracker.db`. Initialize the local
Phase 2 tables explicitly with:

```powershell
python -c "from app.db import create_tables, engine; create_tables(engine)"
```

`CaseService` receives a SQLAlchemy `Session` from its caller. Each mutating command
commits its case changes, history events, and related rows together, and rolls the
whole transaction back on failure.

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
