# Open Loop Tracker

Phase 7 connects the local n8n foundation to the accepted delivery lifecycle for
client handoff. FastAPI remains the only boundary for case state, idempotency,
business events, and persistence.

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

Prerequisites: Docker Desktop or Docker Engine with Docker Compose v2. No paid
services or external credentials are required.

```powershell
docker compose up --build
```

This starts:

- FastAPI at `http://localhost:8000` (`GET /health`)
- n8n Community Edition at `http://localhost:5678`

The backend stores SQLite data in the `open_loop_data` volume. n8n stores its
local application and workflow state in `n8n_data`, so `docker compose restart
n8n` does not erase imported workflows. Inside Compose, n8n calls FastAPI through
`BACKEND_BASE_URL=http://backend:8000`; copy `.env.example` to `.env` only if you
need to override the documented defaults.

### Case Intake demo

1. Open n8n, complete its local owner setup, and import
   `n8n/workflows/case-intake.json`.
2. Activate the imported **Case Intake** workflow.
3. Call its production webhook:

```powershell
$body = @{
    original_message = "Where is my order?"
    moderator_id = "moderator-1"
    client_contact_id = "client-1"
} | ConvertTo-Json

$created = Invoke-RestMethod `
    -Method Post `
    -Uri http://localhost:5678/webhook/case-intake `
    -ContentType application/json `
    -Body $body
$created
```

The response contains the backend-created `public_id` and `status`. Verify the
persisted case through FastAPI:

```powershell
Invoke-RestMethod "http://localhost:8000/api/v1/cases/$($created.public_id)"
```

Verify n8n can reach the backend over the Compose network:

```powershell
docker compose exec n8n node -e 'fetch(process.env.BACKEND_BASE_URL.concat(`/health`)).then(async response => { console.log(response.status, await response.text()); process.exit(response.ok ? 0 : 1) })'
```

To verify n8n persistence, restart only that service, reopen the editor, and
confirm the imported active **Case Intake** workflow is still present:

```powershell
docker compose restart n8n
docker compose ps
```

The workflow forwards the required `original_message` and the optional accepted
case fields. FastAPI remains authoritative for validation. A backend error stops
the HTTP Request node and produces a failed, non-successful webhook execution;
the workflow does not retry or create a second case.

### Client handoff demo

Import and activate `n8n/workflows/send-client-request.json` and
`n8n/workflows/client-reply-intake.json` alongside **Case Intake**. Create a
routed case and send its request through the deterministic local transport:

```powershell
$case = Invoke-RestMethod -Method Post `
    -Uri http://localhost:8000/api/v1/cases `
    -ContentType application/json `
    -Body (@{
        original_message = "Please check the order status"
        moderator_id = "moderator-1"
        client_contact_id = "client-1"
    } | ConvertTo-Json)

$clientDelivery = Invoke-RestMethod -Method Post `
    -Uri http://localhost:5678/webhook/send-client-request `
    -ContentType application/json `
    -Body (@{ public_id = $case.public_id } | ConvertTo-Json)
$clientDelivery

Invoke-RestMethod "http://localhost:8000/api/v1/cases/$($case.public_id)"
```

The case must now be `WAITING_CLIENT`. Submit a client reply; this records the
reply first, then sends the moderator notification through the same delivery
lifecycle:

```powershell
$replyBody = @{
    public_id = $case.public_id
    external_message_id = "demo-client-reply-1"
    text = "The order ships tomorrow"
    sender_id = "client-1"
} | ConvertTo-Json

$reply = Invoke-RestMethod -Method Post `
    -Uri http://localhost:5678/webhook/client-reply-intake `
    -ContentType application/json `
    -Body $replyBody
$reply

$events = Invoke-RestMethod `
    "http://localhost:8000/api/v1/cases/$($case.public_id)/events"
$events | Select-Object event_type, metadata
```

The case remains `WAITING_MODERATOR`; event history contains one each of
`CLIENT_REQUEST_SENT`, `CLIENT_REPLY_RECEIVED`, and `MODERATOR_NOTIFIED`.
Submitting `$replyBody` again reuses the same backend delivery identity and does
not add another reply or moderator business event.

To exercise the required client-delivery failure path, create another routed
case and invoke `send-client-request` with `simulate_failure = $true`:

```powershell
$failedCase = Invoke-RestMethod -Method Post `
    -Uri http://localhost:8000/api/v1/cases `
    -ContentType application/json `
    -Body (@{
        original_message = "Exercise the failure path"
        moderator_id = "moderator-1"
        client_contact_id = "client-1"
    } | ConvertTo-Json)

Invoke-RestMethod -Method Post `
    -Uri http://localhost:5678/webhook/send-client-request `
    -ContentType application/json `
    -Body (@{
        public_id = $failedCase.public_id
        simulate_failure = $true
    } | ConvertTo-Json)
```

The workflow returns `delivery_status = FAILED`, the attempt records the
failure, and the case remains `NEW`. No automatic retry is scheduled.

### SLA watchdog and moderator closure demo

Import and activate `n8n/workflows/sla-watchdog.json` and
`n8n/workflows/moderator-answer-confirmation.json`. The watchdog polls once per
minute for local demonstration; that interval is orchestration configuration,
not a business SLA. It also has a Manual Trigger for repeat/duplicate checks.

To avoid waiting for the normal 2-hour and 24-hour thresholds, copy
`.env.example` to `.env` and use these demo-only values before starting Compose:

```dotenv
CLIENT_REMINDER_1_MINUTES=1
CLIENT_REMINDER_2_MINUTES=2
CLIENT_ESCALATION_MINUTES=3
MODERATOR_REMINDER_1_MINUTES=1
MODERATOR_REMINDER_2_MINUTES=2
MODERATOR_ESCALATION_MINUTES=3
SLA_DEMO_FORCE_FAILURE=false
```

Defaults remain `120/360/1440` minutes for client waits and `30/120/240`
minutes for moderator waits. Create a routed case and move it to
`WAITING_CLIENT` through the accepted Phase 7 workflow:

```powershell
$slaClientCase = Invoke-RestMethod -Method Post `
    -Uri http://localhost:8000/api/v1/cases `
    -ContentType application/json `
    -Body (@{
        original_message = "SLA client demo"
        moderator_id = "moderator-sla"
        client_contact_id = "client-sla"
    } | ConvertTo-Json)

Invoke-RestMethod -Method Post `
    -Uri http://localhost:5678/webhook/send-client-request `
    -ContentType application/json `
    -Body (@{ public_id = $slaClientCase.public_id } | ConvertTo-Json)

Start-Sleep -Seconds 70
Invoke-RestMethod `
    "http://localhost:8000/api/v1/cases/$($slaClientCase.public_id)/events"
```

The scheduled watchdog sends and acknowledges client reminder level 1. Execute
**SLA Watchdog** manually twice in n8n; the same level is not delivered or
acknowledged again. Leave this case waiting for three demo minutes to observe
one `CASE_ESCALATED`; it remains `WAITING_CLIENT`.

For the moderator path, create another case, send it to the client, then submit
a reply. After one demo minute the watchdog routes the reminder using the
current backend `moderator_id`:

```powershell
$slaModeratorCase = Invoke-RestMethod -Method Post `
    -Uri http://localhost:8000/api/v1/cases `
    -ContentType application/json `
    -Body (@{
        original_message = "SLA moderator demo"
        moderator_id = "moderator-current"
        client_contact_id = "client-moderator-demo"
    } | ConvertTo-Json)

Invoke-RestMethod -Method Post `
    -Uri http://localhost:5678/webhook/send-client-request `
    -ContentType application/json `
    -Body (@{ public_id = $slaModeratorCase.public_id } | ConvertTo-Json)

Invoke-RestMethod -Method Post `
    -Uri http://localhost:5678/webhook/client-reply-intake `
    -ContentType application/json `
    -Body (@{
        public_id = $slaModeratorCase.public_id
        external_message_id = "sla-moderator-reply-1"
        text = "Moderator can answer now"
        sender_id = "client-moderator-demo"
    } | ConvertTo-Json)

Start-Sleep -Seconds 70
Invoke-RestMethod `
    "http://localhost:8000/api/v1/cases/$($slaModeratorCase.public_id)/events"
```

Confirm the original user was answered, then repeat the same request to verify
idempotent closure:

```powershell
$closeBody = @{ public_id = $slaModeratorCase.public_id } | ConvertTo-Json
Invoke-RestMethod -Method Post `
    -Uri http://localhost:5678/webhook/moderator-answer-confirmation `
    -ContentType application/json -Body $closeBody
Invoke-RestMethod -Method Post `
    -Uri http://localhost:5678/webhook/moderator-answer-confirmation `
    -ContentType application/json -Body $closeBody
Invoke-RestMethod http://localhost:8000/api/v1/actions/due
```

The first response is `CLOSED` with `already_processed=false`; the second is
`CLOSED` with `already_processed=true`. The closed case produces no due action.

For a failure-only smoke test, set `SLA_DEMO_FORCE_FAILURE=true`, recreate n8n
with `docker compose up -d --force-recreate n8n`, and use a fresh due case. The
attempt becomes `FAILED`, no SLA acknowledgement event is written, and later
watchdog polls reuse the failed attempt without creating Phase 9 retries. Reset
the flag to `false` afterward and run
`docker compose up -d --force-recreate n8n` again so the container reloads the
environment. A subsequent `docker compose restart n8n` verifies that the
published schedule and workflows persist in `n8n_data`.

Stop the stack without deleting either named volume:

```powershell
docker compose down
```
