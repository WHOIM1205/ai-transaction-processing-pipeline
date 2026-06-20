# AI-Powered Transaction Processing Pipeline

A backend service that ingests a messy CSV of financial transactions, processes it
**asynchronously** through a Celery job queue, **cleans** the data, **detects
anomalies**, uses **Gemini 1.5 Flash** to classify transactions and write a
narrative summary, and exposes the results via a polling REST API.

**Stack:** FastAPI · PostgreSQL · SQLAlchemy 2.x + Alembic · Redis · Celery · Gemini 1.5 Flash · Docker Compose

---

## Architecture Overview

```
                 ┌──────────────────────────── Docker Compose ───────────────────────────┐
                 │                                                                         │
  client ──HTTP──┼─►  FastAPI (api)  ──┬── writes Job(pending) ──►  PostgreSQL  ◄──┐       │
  (curl)  ◄──────┼─   202 + job_id     │                            (system of    │       │
                 │                      └── enqueue job_id ──► Redis  record)      │ reads/│
                 │                                              │ (broker)         │ writes│
                 │                                              ▼                  │       │
                 │                                      Celery worker ─────────────┘       │
                 │                                      (pipeline)  ──HTTPS──► Gemini 1.5   │
                 └─────────────────────────────────────────────────────────────────────────┘
```

- **FastAPI (`api`)** — HTTP boundary. Validates the upload, stores the file, writes a
  `Job` row, enqueues one Celery task, and serves the status/results/list endpoints.
  Returns in milliseconds; does no heavy work.
- **PostgreSQL** — system of record (`jobs`, `transactions`, `job_summaries`).
- **Redis** — Celery broker + result backend.
- **Celery worker** — runs the processing pipeline (clean → anomaly → classify → summarise → persist)
  and owns the job state machine.
- **Gemini 1.5 Flash** — batched category classification + the narrative/risk summary.
  Behind a mockable interface; **optional** — the system degrades gracefully without a key.

The **API and worker share no in-process state** — they coordinate only through Postgres
(truth) and Redis (work hand-off). The uploaded file is passed by reference (the
`job_id`), following the claim-check pattern; both containers mount a shared `uploads` volume.

### Project layout
```
app/
├── main.py                 # FastAPI app factory; mounts routers, ensures upload dir
├── api/
│   ├── routes_health.py    # /health, /health/ready
│   ├── routes_jobs.py      # upload, list, status, results
│   └── deps.py             # get_db, PaginationParams
├── core/
│   ├── config.py           # pydantic-settings (env-driven)
│   ├── constants.py        # CSV contract, categories, domestic merchants, reason codes
│   └── logging.py          # shared stdout logging
├── db/                     # engine, session, declarative base
├── models/                 # Job, Transaction, JobSummary (+ enums)
├── schemas/                # Pydantic request/response contracts
├── repositories/           # all DB access (job_repo, transaction_repo)
├── services/               # pipeline stages: cleaning, anomaly, classifier,
│                           #   summary, persistence, pipeline (orchestrator), job_service
├── llm/                    # Gemini client, prompts, retry helper
└── workers/                # celery_app (config) + tasks.process_job
alembic/                    # migration env + versions/
```

---

## Setup Instructions

### Prerequisites
- Docker + Docker Compose (only requirement for the standard run).

### Run everything with one command
```bash
git clone <repo-url>
cd <repo>
docker compose up --build
```
This starts **postgres → redis** (waited on via healthchecks) then **api + worker**.
The API applies database migrations on startup. No manual steps.

- API:        http://localhost:8000
- Swagger UI:  http://localhost:8000/docs
- Health:      http://localhost:8000/health

> **Enable Gemini (optional):** export a key before `up`, or put it in a `.env` file:
> ```bash
> export GEMINI_API_KEY=your_key_here
> docker compose up --build
> ```
> Without a key the pipeline still runs end-to-end; classification rows are marked
> `llm_failed` and the narrative/risk are left null (graceful degradation).

### Local development (without Docker)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # point POSTGRES_HOST/REDIS_HOST at localhost services
alembic upgrade head
uvicorn app.main:app --reload                                   # terminal 1: API
celery -A app.workers.celery_app:celery_app worker --loglevel=INFO  # terminal 2: worker
```

### Running tests
The cleaning/anomaly tests are pure unit tests and the API tests stub external
services, so the suite runs with no database or broker:
```bash
pip install -r requirements.txt
pytest -q
```

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `local` | Environment label. |
| `LOG_LEVEL` | `INFO` | Log verbosity. |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `localhost` / `5432` | DB host/port (compose sets `postgres`). |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `postgres` / `postgres` / `transactions` | DB credentials. |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | `localhost` / `6379` / `0` | Redis (compose sets `redis`). |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | *(derived from Redis)* | Override broker/backend. |
| `GEMINI_API_KEY` | *(empty)* | Gemini key. Empty ⇒ LLM skipped gracefully. |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Model id. |
| `LLM_BATCH_SIZE` | `25` | Transactions per classification call. |
| `LLM_MAX_RETRIES` | `3` | Attempts per LLM call (exponential backoff). |
| `LLM_RETRY_BASE_DELAY` | `1.0` | Backoff base seconds. |
| `UPLOAD_DIR` | `/data/uploads` | Where uploaded CSVs are stored (shared volume). |
| `MAX_UPLOAD_BYTES` | `5242880` | Upload size cap (413 above). |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs/upload` | Upload a CSV; validates it, creates a `pending` job, enqueues processing, returns `job_id` (202). |
| `GET` | `/jobs` | List jobs (newest first); `?status=` filter; `?limit=&offset=` pagination. |
| `GET` | `/jobs/{job_id}/status` | Job status + row counts; includes `summary` once completed. |
| `GET` | `/jobs/{job_id}/results` | Full results: job details, status, summary, paginated transactions. |
| `GET` | `/health` · `/health/ready` | Liveness · readiness (checks Postgres + Redis). |

Status codes: `202` accepted · `200` ok · `400` invalid upload · `404` unknown job ·
`409` results requested before completion · `413` file too large · `422` validation error.

---

## curl Examples

```bash
# 1. Upload → returns job_id immediately
curl -s -X POST http://localhost:8000/jobs/upload \
  -F "file=@transactions.csv;type=text/csv"
# {"job_id":"<uuid>","status":"pending","filename":"transactions.csv"}

JOB=<uuid>

# 2. Poll status (pending → processing → completed). Summary appears when done.
curl -s http://localhost:8000/jobs/$JOB/status

# 3. Full results (transactions paginated)
curl -s "http://localhost:8000/jobs/$JOB/results?limit=20&offset=0"

# 4. List jobs, filter by status
curl -s "http://localhost:8000/jobs?status=completed&limit=10"

# Error examples
curl -i -X POST http://localhost:8000/jobs/upload -F "file=@notes.txt"   # 400
curl -i http://localhost:8000/jobs/$JOB/results                          # 409 if not done yet
```

---

## Processing Flow

```
POST /jobs/upload
  └─ validate extension/size/header → save file → INSERT Job(pending) → enqueue → 202 {job_id}

worker: process_job(job_id)
  ├─ Phase A  claim job (SELECT ... FOR UPDATE; idempotency guard) → status=processing
  ├─ Phase B  pipeline (no DB lock held):
  │     1. Clean      normalise dates/amounts/currency/status, fill 'Uncategorised',
  │                   drop exact duplicates → row_count_raw / row_count_clean
  │     2. Anomaly    Rule 1: amount > 3× account median
  │                   Rule 2: USD on a domestic-only merchant (Swiggy/Ola/IRCTC/Zomato)
  │     3. Classify   batch uncategorised rows → Gemini → llm_category (retry×3, backoff;
  │                   exhausted/absent ⇒ llm_failed, job continues)
  │     4. Summarise  deterministic totals/top-merchants/breakdown; Gemini writes
  │                   narrative + risk_level (null on failure)
  └─ Phase C  persist transactions + summary + counts in ONE transaction → status=completed
              (on any error: status=failed + error_message)

GET /jobs/{id}/status  → poll until "completed"
GET /jobs/{id}/results → job details + summary + paginated transactions
```

**Definitions:** "spend" = `SUCCESS` transactions only; INR and USD are reported
separately (no FX conversion). Re-delivering a task is safe (idempotent): completed
jobs are skipped and persistence replaces prior results rather than duplicating them.
