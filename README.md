# Open Loop Tracker

Phase 5 adds delivery-attempt tracking and deterministic fixed-delay retry
decisions to the accepted case and SLA APIs. External delivery integrations
remain deferred.

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

SLA thresholds are configured in minutes. Defaults are client reminders at 120
and 360 minutes with escalation at 1440 minutes, and moderator reminders at 30
and 120 minutes with escalation at 240 minutes. Override them with:

- `CLIENT_REMINDER_1_MINUTES`
- `CLIENT_REMINDER_2_MINUTES`
- `CLIENT_ESCALATION_MINUTES`
- `MODERATOR_REMINDER_1_MINUTES`
- `MODERATOR_REMINDER_2_MINUTES`
- `MODERATOR_ESCALATION_MINUTES`

Each existing deadline column stores the first reminder deadline for its active
wait. Later thresholds are derived from that baseline. Client deadlines start on
`NEW -> WAITING_CLIENT`; client reply clears that deadline and starts the
moderator deadline; close or cancellation clears both.

Delivery retries default to 3 total attempts with a 5-minute fixed delay. Set
`DELIVERY_MAX_ATTEMPTS` and `DELIVERY_RETRY_DELAY_MINUTES` to override the policy.
Retry eligibility is derived from the previous failed attempt's `completed_at`;
the backend reserves and tracks attempts but never executes external delivery.

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
- `GET /api/v1/actions/due`
- `POST /api/v1/actions/{case_public_id}/ack`
- `POST /api/v1/deliveries/attempts`
- `POST /api/v1/deliveries/{idempotency_key}/attempts/{attempt_number}/result`
- `GET /api/v1/deliveries/retryable`

Mutation responses include `already_processed` so callers can safely interpret
supported command retries. The API does not perform external message delivery.
The due-action endpoint is read-only and returns only the highest reached,
unacknowledged threshold per active case. The acknowledgement endpoint records a
reminder or escalation event only after external execution succeeds; it does not
change case state.
Delivery start and result commands are idempotent. Failed attempts and reserved
retries write `DELIVERY_FAILED` and `DELIVERY_RETRIED` events atomically with the
attempt mutation. The retryable endpoint is read-only.

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
