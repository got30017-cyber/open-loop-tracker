# Open Loop Tracker

Phase 3 provides the FastAPI application, infrastructure-independent case state
machine, SQLite persistence, an injected-session application service, and a
versioned REST API with explicit idempotent command semantics. Operational
integrations remain deferred.

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

## REST API

Case operations are available under `/api/v1/cases`:

- `POST /api/v1/cases`
- `GET /api/v1/cases/{public_id}`
- `POST /api/v1/cases/{public_id}/send-to-client`
- `POST /api/v1/cases/{public_id}/client-reply`
- `POST /api/v1/cases/{public_id}/user-answered`
- `POST /api/v1/cases/{public_id}/cancel`
- `POST /api/v1/cases/{public_id}/reassign`
- `GET /api/v1/cases/{public_id}/events`

Mutation responses include `already_processed` so callers can safely interpret
supported command retries. The API does not perform external message delivery.

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
