# AI-Powered Transaction Processing Pipeline — Architecture & Engineering Design

> Engineering design document. No implementation code. Target: implementable in 3–4 days, reviewable by senior backend engineers.

**Stack:** FastAPI · PostgreSQL · Redis · Celery · Gemini 1.5 Flash · Docker Compose

---

## 0. Problem Restated (grounded in the actual data)

We ingest a dirty CSV (~90 rows) of financial transactions, process it **asynchronously**, and expose results via a **polling API**. The data is deliberately messy — confirmed by inspecting `transactions.csv`:

- **Three** date formats, not two: `04-09-2024` (DD-MM-YYYY), `2024/02/05` (YYYY/MM/DD), and `2024-07-15` (ISO; the `TXN2000*` rows).
- `$`-prefixed amounts (`$11325.79`), lowercase currency (`inr`), mixed-case status (`success`/`SUCCESS`/`PENDING`).
- Exact duplicate rows (`TXN1009`, `TXN1035`, `TXN1079`, … each appear twice), blank `txn_id` (4 rows), blank `category`.
- Planted outliers: `TXN2000`-series amounts of 91k–193k vs. a typical 1k–15k band.
- Currency anomalies: `Zomato` (domestic-only) charged in `USD`.

These observations drive concrete decisions in cleaning, anomaly detection, and the data model below.

---

## 1. High-Level Architecture

```
                          ┌─────────────────────────────────────────────┐
                          │                  Docker Compose              │
                          │                                              │
   ┌────────┐  HTTP       │   ┌──────────────┐        ┌──────────────┐   │
   │ Client │ ─────────►  │   │   FastAPI     │        │  PostgreSQL  │   │
   │ (curl) │ ◄─────────  │   │  (API layer)  │ ◄────► │  (system of  │   │
   └────────┘  job_id /   │   └──────┬───────┘        │   record)    │   │
               status /   │          │  enqueue       └──────▲───────┘   │
               results    │          ▼  (job_id)              │ persist  │
                          │   ┌──────────────┐                │          │
                          │   │    Redis     │                │          │
                          │   │ broker +     │                │          │
                          │   │ result back  │                │          │
                          │   └──────┬───────┘                │          │
                          │          │ dequeue               │          │
                          │          ▼                        │          │
                          │   ┌──────────────┐   HTTPS  ┌────────────┐   │
                          │   │ Celery Worker │ ───────► │  Gemini    │   │
                          │   │  (pipeline)   │ ◄─────── │ 1.5 Flash  │   │
                          │   └──────────────┘  JSON    └────────────┘   │
                          └─────────────────────────────────────────────┘
```

### Component responsibilities

| Component | Responsibility | Explicitly NOT responsible for |
|---|---|---|
| **FastAPI** | HTTP boundary. Validates uploads, writes the `Job` row, enqueues one Celery task carrying `job_id`, serves status/results/list endpoints. Must respond in **milliseconds**. | Any CSV parsing, LLM calls, or heavy compute. |
| **PostgreSQL** | System of record. Stores `Job`, `Transaction`, `JobSummary`. Source of truth for status and results. | Queuing, ephemeral state. |
| **Redis** | Celery **message broker** (task queue) + **result backend** (task state). Optional: cache the `GET /status` payload to absorb polling. | Durable data — Redis is treated as ephemeral. |
| **Celery Worker** | Executes the processing pipeline (validate → clean → detect → classify → summarize → persist). Owns all long-running work and retries. | Serving HTTP. |
| **Gemini 1.5 Flash** | LLM for (a) batched category classification of uncategorised rows and (b) one narrative-summary call returning structured JSON. | Being a hard dependency — the job must survive Gemini failure (`llm_failed`). |
| **Docker Compose** | Single-command orchestration of all four services + healthcheck-based startup ordering and shared volume for uploaded files. | Production scheduling/scaling. |

### How they communicate

1. **Client → FastAPI:** REST/HTTP (`multipart/form-data` for upload, JSON for reads).
2. **FastAPI → PostgreSQL:** SQL over a connection pool (writes the `Job`, reads status/results).
3. **FastAPI → Redis:** Celery `.delay(job_id)` pushes a task message onto the broker.
4. **Redis → Worker:** the worker long-polls the broker and dequeues the task.
5. **Worker → PostgreSQL:** reads the job, bulk-inserts transactions, writes the summary, flips status.
6. **Worker → Gemini:** outbound HTTPS with structured-JSON requests.
7. **Worker → Redis:** task state/result is written back to the result backend.

> **Key principle:** the API and the worker share **no in-process state**. They coordinate only through Postgres (truth) and Redis (work hand-off). The `job_id` is the single correlation token across the whole system.

---

## 2. End-to-End Request Lifecycle (`POST /jobs/upload`)

```
[1] Client POSTs multipart CSV
        │
        ▼
[2] FastAPI: synchronous validation (fast-fail)
      • content-type is text/csv or .csv extension
      • size <= MAX_UPLOAD_BYTES (e.g. 5 MB guard)
      • non-empty; header row contains the expected columns
      • (cheap) UTF-8 decodable
        │ invalid → 400/422, NO job created
        ▼
[3] FastAPI persists the raw file to the shared uploads volume
      • path: /data/uploads/{job_id}.csv
      • we store the FILE, not the parsed contents
        │
        ▼
[4] FastAPI INSERT Job(status='pending', filename, row_count_raw=NULL, created_at)
      • commit → job_id (UUID) now durable
        │
        ▼
[5] FastAPI enqueues exactly one Celery task: process_job.delay(job_id)
      • message body carries ONLY job_id (small, idempotent)
        │
        ▼
[6] FastAPI returns 202 Accepted { job_id, status: "pending" }   ← request ends here
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
[7] Worker dequeues task → loads Job by id
      • idempotency guard: if status is already 'processing'/'completed', skip
      • UPDATE Job.status = 'processing'
        │
        ▼
[8] PIPELINE (see §7): validate → clean → anomaly → LLM classify → LLM summary → persist
        │
        ▼
[9] Worker, in ONE transaction:
      • bulk INSERT cleaned Transactions
      • INSERT JobSummary
      • UPDATE Job.status='completed', row_count_clean, completed_at
        │ on unrecoverable error → UPDATE Job.status='failed', error_message
        ▼
[10] (optional) invalidate/refresh cached status in Redis
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
[11] Client polls GET /jobs/{job_id}/status → "completed"
[12] Client GET /jobs/{job_id}/results → full structured output
```

**Why pass only `job_id` (not the rows) through the queue:** keeps the broker message tiny, avoids serialising large payloads into Redis, and makes the task re-runnable — the worker always reloads canonical input from disk + DB. This is the standard "claim-check" pattern.

---

## 3. Folder Structure

```
transaction-pipeline/
├── docker-compose.yml          # single-command orchestration of all 4 services
├── Dockerfile                  # one image, run as either API or worker via command
├── .env.example                # documented env vars (DB url, Redis url, GEMINI_API_KEY)
├── README.md                   # setup + example curl requests (required by brief)
├── pyproject.toml / requirements.txt
│
└── app/
    ├── main.py                 # FastAPI app factory, router registration, lifespan
    │
    ├── api/                    # HTTP layer ONLY — thin controllers, no business logic
    │   ├── routes_jobs.py      # /jobs/upload, /jobs/{id}/status, /jobs/{id}/results, /jobs
    │   └── deps.py             # shared dependencies (DB session, pagination, filters)
    │
    ├── core/                   # cross-cutting concerns
    │   ├── config.py           # pydantic-settings: env-driven config, single source
    │   ├── logging.py          # structured logging (job_id in every log line)
    │   └── constants.py        # category enum, domestic-merchant set, batch sizes
    │
    ├── db/                     # persistence plumbing
    │   ├── session.py          # engine + session factory + pool config
    │   └── base.py             # declarative base / metadata
    │
    ├── models/                 # ORM table definitions (Job, Transaction, JobSummary)
    │   ├── job.py
    │   ├── transaction.py
    │   └── summary.py
    │
    ├── schemas/                # Pydantic request/response contracts (API I/O shapes)
    │   ├── job.py              # JobOut, JobStatusOut, JobListItem
    │   ├── results.py          # ResultsOut, anomaly, category breakdown
    │   └── llm.py              # strict schemas for Gemini structured output
    │
    ├── repositories/           # DB access functions — keeps SQL out of routes/services
    │   ├── job_repo.py
    │   └── transaction_repo.py
    │
    ├── services/               # PIPELINE business logic (pure-ish, unit-testable)
    │   ├── validation.py       # Stage 1: structural CSV validation
    │   ├── cleaning.py         # Stage 2: normalisation + dedup
    │   ├── anomaly.py          # Stage 3: outlier + currency-mismatch rules
    │   ├── classifier.py       # Stage 4: orchestrates batched LLM categorisation
    │   ├── summary.py          # Stage 5: builds aggregates + narrative call
    │   └── persistence.py      # Stage 6: bulk write + status transition
    │
    ├── llm/                    # Gemini integration, isolated behind an interface
    │   ├── client.py           # thin wrapper: request, structured-JSON config, timeout
    │   ├── prompts.py          # versioned prompt templates
    │   └── retry.py            # exponential backoff + jitter, fallback policy
    │
    └── workers/                # Celery wiring
        ├── celery_app.py       # Celery instance, broker/backend config, queues
        └── tasks.py            # process_job task: thin orchestrator calling services/

tests/
├── test_cleaning.py           # date/amount/currency/status normalisation, dedup
├── test_anomaly.py            # 3x-median + USD-domestic rules
├── fixtures/sample.csv        # trimmed copy of the provided data + edge cases
└── test_api.py                # upload → status → results contract tests
```

**Layering rule (why this structure):** `api → services → repositories → models`. Routes never touch the ORM directly; services never touch HTTP. The Celery task and an HTTP request can both invoke the same `services/` functions, so business logic is tested **without** Celery, Redis, or a live LLM. `llm/` is isolated behind a thin client so Gemini can be swapped or mocked.

---

## 4. Database Design

Three tables. UUID primary keys for `Job` (safe to expose in URLs, no enumeration); `BIGSERIAL` for the high-volume `Transaction` table (compact, sequential, better index locality).

### 4.1 `jobs`

| Column | Type | Constraints | Why it exists |
|---|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` | Public job handle returned to client; used as queue correlation token. |
| `filename` | `TEXT` | NOT NULL | Echoed in `GET /jobs`; traceability of the source upload. |
| `status` | `job_status` (enum) | NOT NULL, default `'pending'` | Drives the polling API. Enum: `pending`, `processing`, `completed`, `failed`. |
| `row_count_raw` | `INTEGER` | NULL until parsed | Rows read from CSV (pre-dedup). Lets reviewers see "dirty in". |
| `row_count_clean` | `INTEGER` | NULL until done | Rows after dedup/cleaning. "Clean out" — together they show data quality. |
| `error_message` | `TEXT` | NULL | Populated only on `failed`; surfaced for debugging. |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, default `now()` | Listing/sort; `tz`-aware to avoid ambiguity. |
| `completed_at` | `TIMESTAMPTZ` | NULL | Set on terminal state; enables processing-duration metrics. |

**Indexes:** PK on `id`; `INDEX (status)` for `GET /jobs?status=`; `INDEX (created_at DESC)` for default listing order.

### 4.2 `transactions`

One row per cleaned, de-duplicated transaction.

| Column | Type | Constraints | Why it exists |
|---|---|---|---|
| `id` | `BIGSERIAL` | PK | Internal surrogate key; high-volume table. |
| `job_id` | `UUID` | FK → `jobs.id` ON DELETE CASCADE, NOT NULL | Scopes every row to its job; cascade keeps cleanup simple. |
| `txn_id` | `TEXT` | NULL | Source ID — **nullable** because the CSV has blanks; we do not invent IDs. |
| `date` | `DATE` | NULL | Normalised to ISO 8601 from 3 input formats; NULL if unparseable. |
| `merchant` | `TEXT` | NOT NULL | Used for top-merchants and the USD-domestic rule. |
| `amount` | `NUMERIC(14,2)` | NOT NULL, CHECK `amount >= 0` | **NUMERIC, never float** — money. Width covers the 193k outliers w/ headroom. |
| `currency` | `currency_enum` | NOT NULL | Normalised upper-case `INR`/`USD`. |
| `status` | `txn_status` (enum) | NOT NULL | Normalised upper-case `SUCCESS`/`FAILED`/`PENDING`. |
| `category` | `TEXT` | NOT NULL, default `'Uncategorised'` | Original category; blanks filled with `Uncategorised`. |
| `account_id` | `TEXT` | NOT NULL | Grouping key for the per-account median anomaly rule. |
| `notes` | `TEXT` | NULL | Free text (`SUSPICIOUS`, `Duplicate?`); a soft signal, not authoritative. |
| `is_anomaly` | `BOOLEAN` | NOT NULL, default `false` | Fast filtering of flagged rows in `/results`. |
| `anomaly_reason` | `TEXT[]` | NULL | **Array** — a row can be flagged by multiple rules at once. |
| `llm_category` | `TEXT` | NULL | Category assigned by Gemini (only for rows that were blank). |
| `llm_failed` | `BOOLEAN` | NOT NULL, default `false` | True if this row's LLM batch exhausted retries. |
| `llm_raw_response` | `JSONB` | NULL | Audit trail of the model's raw output for that row's batch. |

**Indexes:** PK on `id`; `INDEX (job_id)` (every results query filters by it); partial `INDEX (job_id) WHERE is_anomaly` for fast anomaly retrieval; `INDEX (job_id, account_id)` to support the per-account median grouping.

**Effective category (design note):** the value served in `/results` is `COALESCE(NULLIF(category,'Uncategorised'), llm_category, 'Uncategorised')`. We **keep the original and the LLM value in separate columns** rather than overwriting — reviewers can see exactly what the model contributed, and re-running classification is non-destructive.

### 4.3 `job_summaries`

One row per job (1:1). Holds the aggregate report.

| Column | Type | Constraints | Why it exists |
|---|---|---|---|
| `id` | `BIGSERIAL` | PK | Surrogate key. |
| `job_id` | `UUID` | FK → `jobs.id` ON DELETE CASCADE, **UNIQUE**, NOT NULL | Enforces 1:1 with the job. |
| `total_spend_inr` | `NUMERIC(16,2)` | NOT NULL, default 0 | Per-currency totals kept in separate columns (no FX conversion — mixing currencies is wrong). |
| `total_spend_usd` | `NUMERIC(16,2)` | NOT NULL, default 0 | As above. |
| `top_merchants` | `JSONB` | NOT NULL, default `[]` | Top-3 `[{merchant, total, count}]` — variable shape, JSONB fits. |
| `category_breakdown` | `JSONB` | NOT NULL, default `{}` | Per-category spend (required by `/results`). |
| `anomaly_count` | `INTEGER` | NOT NULL, default 0 | Count of flagged rows; cheap headline stat for `/status`. |
| `narrative` | `TEXT` | NULL | 2–3 sentence LLM narrative; NULL if the summary call failed. |
| `risk_level` | `risk_enum` | NULL | `low`/`medium`/`high` from the LLM; NULL on failure. |

**Relationships:** `jobs 1—N transactions` and `jobs 1—1 job_summaries`. All children cascade on job delete.

> **Spend semantics:** "spend" = transactions with `status = SUCCESS`. `FAILED`/`PENDING` rows are stored but excluded from spend totals; this is stated explicitly so reviewers don't have to guess.

---

## 5. API Design

All responses JSON. Base path `/`. Errors use a consistent envelope: `{ "detail": "<message>" }` (FastAPI default) or `{ "detail": [ ...validation... ] }` for 422.

### 5.1 `POST /jobs/upload`

- **Request:** `multipart/form-data`, field `file` = the CSV.
- **Success `202 Accepted`:**
  ```json
  { "job_id": "f7c1...uuid", "status": "pending", "filename": "transactions.csv" }
  ```
- **Status codes:** `202` accepted & enqueued · `400` not a CSV / empty / missing required header columns · `413` file exceeds size limit · `422` malformed multipart.
- **Error example `400`:** `{ "detail": "Missing required columns: amount, currency" }`

### 5.2 `GET /jobs/{job_id}/status`

- **Request:** path param `job_id`.
- **Success `200`:**
  ```json
  {
    "job_id": "f7c1...",
    "status": "completed",
    "row_count_raw": 95,
    "row_count_clean": 83,
    "summary": {                 // present only when status == "completed"
      "anomaly_count": 6,
      "total_spend_inr": 812340.55,
      "total_spend_usd": 49210.00,
      "risk_level": "high"
    }
  }
  ```
- **Status codes:** `200` · `404` unknown `job_id`.
- For `pending`/`processing`/`failed`, `summary` is `null`; `failed` includes `error_message`.

### 5.3 `GET /jobs/{job_id}/results`

- **Success `200`:**
  ```json
  {
    "job_id": "f7c1...",
    "transactions": [
      { "txn_id": "TXN2001", "date": "2024-07-15", "merchant": "Flipkart",
        "amount": 146100.68, "currency": "INR", "status": "SUCCESS",
        "category": "Shopping", "account_id": "ACC005",
        "is_anomaly": true, "anomaly_reason": ["amount_gt_3x_account_median"],
        "llm_failed": false }
    ],
    "anomalies": [ /* subset where is_anomaly == true */ ],
    "category_breakdown": { "Shopping": 210340.10, "Food": 88123.40, "...": 0 },
    "summary": {
      "total_spend_inr": 812340.55, "total_spend_usd": 49210.00,
      "top_merchants": [ {"merchant":"IRCTC","total":221000.0,"count":9} ],
      "anomaly_count": 6,
      "narrative": "Spending is concentrated in Shopping and Travel...",
      "risk_level": "high"
    }
  }
  ```
- **Status codes:** `200` · `404` unknown job · `409 Conflict` if job not yet `completed` (`{ "detail": "Job not completed; current status: processing" }`).

### 5.4 `GET /jobs`

- **Query params:** `status` (optional filter), `limit`/`offset` (pagination, sensible defaults `limit=50`).
- **Success `200`:**
  ```json
  {
    "items": [
      { "job_id":"f7c1...", "filename":"transactions.csv", "status":"completed",
        "row_count_raw":95, "created_at":"2026-06-20T09:12:03Z" }
    ],
    "total": 1, "limit": 50, "offset": 0
  }
  ```
- **Status codes:** `200` · `422` invalid `status` value.

---

## 6. Celery Architecture

### Queue structure
- **One broker (Redis), two logical queues** is sufficient and not over-engineered:
  - `default` — the `process_job` orchestrator task.
  - (Optional, scale-ready) `llm` — if we later split LLM batch calls into subtasks, they route here so a slow/throttled Gemini can't starve job intake. For the 3–4 day build, a **single `default` queue** is acceptable; the second queue is a one-line config change later.
- Result backend: Redis, with a TTL on results (we don't rely on it for truth — Postgres is truth).

### Task flow
A **single coarse-grained task** `process_job(job_id)` runs the whole pipeline sequentially. Rationale: ~90 rows is tiny; a chain/chord of subtasks adds coordination complexity with no benefit at this scale. The pipeline stages are plain functions in `services/`, so we can later promote stages to subtasks without rewriting logic.

### Retry strategy (two distinct layers)
1. **Inside the task — LLM calls only:** each Gemini batch retries up to **3×** with exponential backoff + jitter (see §8). Exhaustion ⇒ mark that batch `llm_failed=true` and **continue** (per brief: never fail the whole job for LLM).
2. **Task-level:** retry only on **transient infrastructure** errors (DB connection blip, Redis hiccup) — `max_retries=3`, exponential backoff. **Do not** auto-retry on data/validation errors — those are deterministic and would just fail again; they go straight to `status='failed'`.

### Failure handling
- **Validation failure** (bad CSV): `status='failed'`, `error_message` set, no retry.
- **Partial LLM failure:** job still `completed`; affected rows carry `llm_failed=true`; summary `narrative`/`risk_level` may be `null`. The API surfaces this honestly rather than hiding it.
- **Hard crash mid-task** (worker killed): task is redelivered (with `acks_late=true`); the idempotency guard makes the re-run safe.
- **Visibility timeout:** set broker visibility timeout > worst-case task runtime so long jobs aren't redelivered while still running.

### Idempotency considerations
- **State guard:** on entry, if `Job.status` is already `completed`, return immediately; if `processing`, the row was claimed (guarded by `SELECT ... FOR UPDATE` / a compare-and-set on status).
- **Write idempotency:** persistence runs in a single transaction that **deletes any existing `transactions`/`job_summaries` for that `job_id` before re-inserting** (or upserts). A re-delivered task therefore converges to the same final state — no duplicate rows.
- **`acks_late=true` + `reject_on_worker_lost=true`** so a crashed task is re-run rather than silently lost, which is only safe *because* of the above.

---

## 7. Processing Pipeline Design

The task is a thin orchestrator; each stage is an isolated, testable function. Stages run in order; the first hard failure (stages 1) aborts to `failed`, while LLM stages (4–5) degrade gracefully.

**Stage 1 — CSV Validation (structural, fail-fast).**
Confirm the file is parseable CSV, non-empty, and the header contains the required columns. Capture `row_count_raw`. This is the only stage that can mark the whole job `failed`. Responsibility: guarantee downstream stages receive well-formed tabular input.

**Stage 2 — Data Cleaning / Normalisation.**
- Dates → ISO 8601, handling all three observed formats (`DD-MM-YYYY`, `YYYY/MM/DD`, `YYYY-MM-DD`); unparseable → `NULL`.
- Amounts → strip `$`/whitespace/thousands separators → `NUMERIC`.
- `currency` → upper-case (`inr`→`INR`); `status` → upper-case.
- Blank `category` → `Uncategorised`.
- **Remove exact duplicate rows** (all source columns equal) — record `row_count_clean`.
Responsibility: produce a canonical, typed record set. Pure function over rows → easy unit tests.

**Stage 3 — Anomaly Detection (deterministic rules, no LLM).**
- **Statistical outlier:** per `account_id`, compute median amount; flag rows where `amount > 3 × median` → reason `amount_gt_3x_account_median`. (This catches the planted `TXN2000*` 91k–193k rows.)
- **Currency mismatch:** `currency == USD` AND merchant ∈ domestic-only set (`Swiggy`, `Ola`, `IRCTC`, `Zomato`, `Jio Recharge`, …) → reason `usd_on_domestic_merchant`.
- `anomaly_reason` is an **array**; a row may trip both. Sets `is_anomaly`.
Responsibility: cheap, explainable flags computed before any LLM cost.

**Stage 4 — LLM Classification (batched).**
Select only rows whose effective category is `Uncategorised`. Group into batches (e.g. 25 rows/call) and ask Gemini to return structured JSON mapping each row to one of the 8 allowed categories. Per-batch retry/backoff; on exhaustion mark the batch `llm_failed` and move on. Writes `llm_category`. Responsibility: minimise LLM calls (batching) while never blocking the job.

**Stage 5 — Narrative Summary (single LLM call).**
Compute deterministic aggregates locally first (per-currency `SUCCESS` totals, top-3 merchants, anomaly count, category breakdown). Pass these aggregates — **not raw rows** — to one Gemini call that returns a structured JSON `{ narrative, risk_level }`. We compute numbers ourselves (trustworthy, cheap) and only use the LLM for prose + risk judgement. On failure: store aggregates, leave `narrative`/`risk_level` `NULL`.

**Stage 6 — Database Persistence (atomic).**
In **one transaction**: delete any prior rows for this `job_id` (idempotency), bulk-insert `transactions`, insert `job_summaries`, update `jobs.row_count_clean`. Either all of it lands or none does. Responsibility: atomic, idempotent commit of results.

**Stage 7 — Job Completion.**
Within the same transaction, set `status='completed'`, `completed_at=now()`. Optionally refresh the cached status in Redis. Any uncaught exception in stages 2–6 → rollback, `status='failed'`, `error_message`. Responsibility: deterministic terminal state the polling API can rely on.

---

## 8. Gemini 1.5 Flash Integration Design

**Category classification workflow.**
Input = the subset of rows lacking a category (post-cleaning). For each batch, the prompt supplies a compact list of `{row_ref, merchant, amount, notes}` and the **closed set** of 8 allowed categories, instructing the model to return JSON keyed by `row_ref`. We map results back by `row_ref`, never by trusting order.

**Batching strategy.**
- One call per batch of ~**20–30 rows** (the whole 90-row file is ≤ ~4 calls). Batch size is a config constant, tuned to stay well under token limits and rate caps.
- Classification batches are independent — safe to parallelise later; sequential is fine now.

**Structured JSON output.**
- Use Gemini's structured-output mode (`response_mime_type = application/json` + a `response_schema`) so the model returns schema-conformant JSON, not prose.
- **Validate every response against a strict Pydantic schema.** Any category outside the 8-value enum is coerced to `Other`. We never write an unvalidated model string into the DB.

**Retry mechanism.**
- Up to **3 attempts** per call, exponential backoff with jitter (e.g. ~1s, 2s, 4s ± jitter).
- Retry on: timeouts, `429` rate-limit, `5xx`, malformed/unparseable JSON.
- Do **not** retry on auth/`400` config errors — fail fast and log.
- Per-call timeout so a hung request can't stall the worker.

**Fallback behavior (graceful degradation — never fail the job).**
- Classification batch exhausts retries → those rows get `llm_failed=true`, effective category falls back to `Uncategorised`/`Other`; job continues.
- Summary call fails → store computed aggregates, `narrative=NULL`, `risk_level=NULL`.
- Missing/empty `GEMINI_API_KEY` → skip LLM stages cleanly and mark rows `llm_failed` (the system still produces cleaned data + deterministic anomalies + aggregates, so it remains demoable without a key).

---

## 9. Docker Compose Design

Four services, **one image** (the API and worker run the same codebase with different start commands).

| Service | Base | Responsibility | Depends on (condition) |
|---|---|---|---|
| `postgres` | `postgres:16` | Durable store. Named volume for data; `POSTGRES_*` env. Exposes a **healthcheck** (`pg_isready`). | — |
| `redis` | `redis:7` | Broker + result backend. Healthcheck (`redis-cli ping`). | — |
| `api` | app image | Runs the ASGI server (uvicorn). Mounts the shared `uploads` volume. Runs DB migrations on start (or an `init` step). | `postgres: healthy`, `redis: healthy` |
| `worker` | app image | Runs `celery worker`. Mounts the **same** `uploads` volume so it can read files the API wrote. | `postgres: healthy`, `redis: healthy` |

**Shared resources.**
- Named volume `uploads:/data/uploads` mounted into **both** `api` and `worker` (claim-check file hand-off).
- Named volume `pgdata` for Postgres durability.
- All services on one Compose network; addressed by service name (`postgres`, `redis`).

**Startup order.**
`postgres` & `redis` start first → become `healthy` → `api` and `worker` start (gated by `depends_on: condition: service_healthy`). `api` applies migrations before serving. This guarantees `docker compose up` works cold with **no manual steps**, satisfying the brief. The worker also tolerates a not-yet-ready broker via Celery's built-in connection retry as a belt-and-suspenders measure.

---

## 10. Sequence Diagram (text)

```
Client            FastAPI            PostgreSQL          Redis           Worker           Gemini
  │                  │                   │                 │               │                │
  │ POST /jobs/upload│                   │                 │               │                │
  ├─────────────────►│                   │                 │               │                │
  │                  │ validate CSV (sync)│                 │               │                │
  │                  │ save file → /data/uploads/{id}.csv   │               │                │
  │                  │ INSERT Job(pending)│                 │               │                │
  │                  ├──────────────────►│                 │               │                │
  │                  │◄──────────────────┤ job_id          │               │                │
  │                  │ enqueue process_job(job_id)          │               │                │
  │                  ├─────────────────────────────────────►│               │                │
  │ 202 {job_id}     │                   │                 │               │                │
  │◄─────────────────┤                   │                 │               │                │
  │                  │                   │                 │ deliver task  │                │
  │                  │                   │                 ├──────────────►│                │
  │                  │                   │ UPDATE status=processing         │                │
  │                  │                   │◄────────────────────────────────┤                │
  │                  │                   │                 │  (validate → clean → anomaly)   │
  │                  │                   │                 │               │ classify batch │
  │                  │                   │                 │               ├───────────────►│
  │                  │                   │                 │               │◄───────────────┤ JSON categories
  │                  │                   │                 │               │ narrative call │
  │                  │                   │                 │               ├───────────────►│
  │                  │                   │                 │               │◄───────────────┤ JSON {narrative,risk}
  │                  │                   │ BEGIN; bulk INSERT txns; INSERT summary;          │
  │                  │                   │ UPDATE Job=completed; COMMIT     │                │
  │                  │                   │◄────────────────────────────────┤                │
  │                  │                   │                 │ ack task      │                │
  │                  │                   │                 │◄──────────────┤                │
  │ GET /status (poll)│                  │                 │               │                │
  ├─────────────────►│ SELECT Job        │                 │               │                │
  │                  ├──────────────────►│                 │               │                │
  │ 200 {completed}  │◄──────────────────┤                 │               │                │
  │◄─────────────────┤                   │                 │               │                │
  │ GET /results     │ SELECT txns+summary│                 │               │                │
  ├─────────────────►├──────────────────►│                 │               │                │
  │ 200 {full report}│◄──────────────────┤                 │               │                │
  │◄─────────────────┤                   │                 │               │                │
```

---

## 11. Scalability Review (traffic × 100)

Assume 100× more uploads and/or far larger files.

### Bottlenecks & failure points (where it breaks first)
1. **Single worker / single-task pipeline.** One coarse task per job, processed largely serially. At 100× the queue backlog grows unbounded → status stuck on `pending`.
2. **Gemini rate limits.** Free-tier RPM/TPM is the hard ceiling. 100× classification calls ⇒ sustained `429`s; retries amplify load (retry storms).
3. **File handling on a shared local volume.** Uploads on a Docker named volume don't scale horizontally and aren't durable; reading whole files into worker memory breaks on large CSVs (memory pressure / OOM).
4. **DB connection pool.** Many API replicas + many workers each holding pooled connections will exhaust Postgres `max_connections`.
5. **Row-by-row / ORM inserts.** Per-row inserts dominate runtime for large files.
6. **Polling load.** Clients hammering `GET /status` hit Postgres directly; at 100× this is a read-amplification hot path.
7. **Synchronous validation in the request path.** Large uploads block the event loop / tie up API workers.

### Proposed improvements (with trade-offs)
| Area | Change | Trade-off |
|---|---|---|
| Throughput | Run **N worker replicas**; split the pipeline into a Celery **chord** — parallel `classify(batch)` subtasks → a `finalize` callback. | More coordination, partial-failure handling complexity. |
| LLM limits | **Token-bucket rate limiter** + dedicated `llm` queue with bounded concurrency; cache classifications by `(merchant, amount-bucket)`. | Caching can misclassify edge cases; added infra. |
| File storage | Move uploads to **object storage (S3/GCS)**; workers **stream-parse** (chunked) instead of loading into memory. | External dependency; more moving parts locally. |
| DB connections | **PgBouncer** (transaction pooling) in front of Postgres; cap pool sizes per service. | Pooler becomes a component to operate; some session features lost. |
| Bulk writes | Use **`COPY` / batched multi-row inserts** instead of ORM per-row. | Bypasses some ORM conveniences. |
| Polling | Cache `/status` in **Redis** (worker writes on transition); add **webhook/SSE** completion callbacks to cut polling. | Cache invalidation; webhook delivery semantics. |
| Big data volume | **Partition `transactions` by `job_id`** (or time); archive old jobs. | Operational overhead of partition management. |
| Ingestion spikes | Stream large uploads straight to object storage; API only validates header + enqueues. | Validation depth in the request path is reduced. |
| Resilience | **Dead-letter queue** for poison tasks; autoscale workers on queue depth. | Requires queue-depth metrics + orchestration (k8s/KEDA). |

> One-line summary for the video: *"At 100× the first three things to break are Gemini rate limits, the single worker, and the in-memory whole-file parse on a local volume — I'd fix them with a rate-limited dedicated LLM queue + horizontal workers using a Celery chord, and stream uploads from object storage."*

---

## 12. Engineering Decisions (and alternatives)

| Choice | Why | Alternatives considered |
|---|---|---|
| **FastAPI** | Async I/O fits an enqueue-and-return API; Pydantic gives request/response validation + auto OpenAPI docs (great for the reviewer's curl session); minimal boilerplate. | **Django REST**: batteries-included (admin, ORM, migrations) but heavier than needed for 4 endpoints; **Flask**: no native async/validation/typing. |
| **PostgreSQL** | Required. Also the right call: ACID for atomic result writes, `NUMERIC` for money, `JSONB` for `top_merchants`/`llm_raw_response`, enums + array types, strong indexing. | **MySQL**: weaker JSON/array story; **SQLite**: not concurrent-write safe for an API+worker setup. |
| **Celery + Redis** | Mature, well-documented async task framework; built-in retries, backoff, `acks_late`, routing — exactly the primitives the brief's retry/failure requirements need. | **RQ**: simpler but fewer features (weaker routing/scheduling); **Dramatiq**: capable but smaller ecosystem; **FastAPI BackgroundTasks**: in-process, dies with the API — unacceptable for durable jobs. |
| **Redis (broker + backend)** | One dependency serves queue + result store + (optional) status cache. Fast, ubiquitous, trivial in Compose. | **RabbitMQ**: stronger delivery guarantees but heavier to operate; overkill here. |
| **Gemini 1.5 Flash** | Free tier (no spend, per brief), fast, **native structured-JSON output** — ideal for deterministic categorisation + summary. | **OpenAI**: needs credits; **Ollama (local)**: no API cost but heavy container + variable quality, slows `docker compose up`. |
| **Docker Compose** | Required; gives the one-command cold start with healthcheck ordering and a shared volume. | Kubernetes/Helm: production-grade but absurd for an internship deliverable. |
| **UUID job id / BIGSERIAL txns** | Non-enumerable public handles; compact sequential keys for the hot table. | All-UUID: larger indexes on the high-volume table for no benefit. |
| **Claim-check (job_id only on queue)** | Tiny messages, re-runnable tasks, truth stays in Postgres. | Passing rows through Redis: bloats broker, breaks idempotency. |

---

## Final Recommendations

### 1. Final recommended architecture
A **single-image, four-service Docker Compose** stack. **FastAPI** is a thin async HTTP boundary that validates the upload, writes a `pending` **Job** to **PostgreSQL**, drops the `job_id` onto a **Redis**-backed **Celery** queue, and returns `202` immediately. One **Celery worker** runs a six-stage pipeline (validate → clean → rule-based anomaly detection → **batched** Gemini classification → single-call Gemini narrative → atomic persist) and flips the job to `completed`/`failed`. **Gemini 1.5 Flash** is used only for category gaps and the prose summary, behind retry + graceful-degradation so it can never fail the job. Numbers are computed in-house; the LLM is used only where it adds value. Truth lives in Postgres; Redis is ephemeral; clients **poll** `/status` then fetch `/results`.

### 2. Recommended implementation order
- **Phase 1 — Skeleton & infra (Day 1).** Repo, Dockerfile, `docker-compose.yml` (api + postgres + redis + worker) booting with healthchecks; config via env; DB migrations; empty `/jobs` endpoints returning stubs. *Exit: `docker compose up` runs clean.*
- **Phase 2 — Upload & job lifecycle (Day 1–2).** `POST /jobs/upload` (validation, file save, Job insert, enqueue), `GET /jobs`, `GET /status`. A no-op Celery task that just flips `pending→processing→completed`. *Exit: end-to-end async loop works without real processing.*
- **Phase 3 — Deterministic pipeline (Day 2).** Stages 1–3 + 6–7: validation, cleaning/dedup, anomaly rules, atomic persistence. Unit tests on cleaning & anomaly using the provided CSV. *Exit: `/results` returns cleaned txns + anomalies + aggregates, no LLM yet.*
- **Phase 4 — Gemini integration (Day 3).** `llm/` client with structured output, batched classification, single-call summary, retry/backoff, `llm_failed` fallback. *Exit: full `/results` incl. narrative + risk_level; degrades cleanly with no API key.*
- **Phase 5 — Hardening & deliverables (Day 3–4).** Idempotency (`acks_late`, pre-delete on persist), status caching (optional), structured logging with `job_id`, README with curl examples, the draw.io diagram, and the 3-minute video script. *Exit: submission-ready.*

### 3. Risks to watch during implementation
1. **`docker compose up` race conditions** — worker/api starting before DB/Redis are truly ready. Mitigate with healthcheck-gated `depends_on` + Celery broker connection retry. (This is the brief's single hardest grading criterion — *"no manual setup steps"* — so verify cold boot early.)
2. **Date parsing ambiguity** — `04-09-2024` is unambiguously DD-MM here, but mixed formats invite bugs; pin format detection and unit-test all three observed shapes + an unparseable case.
3. **Median rule edge cases** — accounts with very few rows make `3×median` unstable; decide behaviour for tiny groups and document it.
4. **Money precision** — never let amounts touch `float`; `NUMERIC` end-to-end (parsing, DB, aggregation).
5. **LLM output drift** — model may return out-of-enum categories or invalid JSON; the strict Pydantic-validate-then-coerce-to-`Other` step is mandatory, not optional.
6. **Idempotency on redelivery** — if a worker dies mid-job, the re-run must not duplicate rows; test by killing the worker mid-task.
7. **Gemini rate limits during the demo** — free tier can `429`; batching + backoff + the no-key fallback keep the demo alive.
8. **Scope creep** — resist building the 100× architecture now; the brief rewards a clean, correct, simple build plus a sharp verbal scaling story.
```
