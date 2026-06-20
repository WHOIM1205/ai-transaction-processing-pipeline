# Final Reviewer Checklist

Every assignment requirement mapped to where it is implemented. Paths are relative
to the repo root.

## Required Stack
| Requirement | Where | Status |
|---|---|---|
| API framework: FastAPI | `app/main.py`, `app/api/routes_jobs.py` | ✅ |
| Database: PostgreSQL | `docker-compose.yml` (postgres), `app/db/session.py` | ✅ |
| Job queue: Celery + Redis | `app/workers/celery_app.py`, `app/workers/tasks.py` | ✅ |
| LLM: Gemini 1.5 Flash (free tier) | `app/llm/client.py` (`GeminiClient`, model `gemini-1.5-flash`) | ✅ |
| Containerisation: Docker + Compose | `Dockerfile`, `docker-compose.yml` | ✅ |
| **Single `docker compose up`**, no manual steps | api runs `alembic upgrade head` then serves; healthcheck-gated startup | ✅ verified |

## API Endpoints
| Requirement | Where | Status |
|---|---|---|
| `POST /jobs/upload` — validate, create Job(pending), enqueue, return job_id | `routes_jobs.py::upload_job` → `services/job_service.py::create_job_from_upload` | ✅ |
| `GET /jobs/{id}/status` — status; summary if completed | `routes_jobs.py::get_job_status`, `schemas/job.py::JobStatusOut` | ✅ |
| `GET /jobs/{id}/results` — cleaned txns, anomalies, breakdown, narrative | `routes_jobs.py::get_job_results`, `schemas/job.py::JobResultsOut` | ✅ |
| `GET /jobs` — list with status filter | `routes_jobs.py::list_jobs`, `repositories/job_repo.py::list_jobs` | ✅ |
| Pagination (list + results transactions) | `api/deps.py::PaginationParams`; `transaction_repo.list_for_job` | ✅ |
| OpenAPI documentation | `summary`/`description`/`responses` on every route → `/docs` | ✅ |

## Processing Pipeline
| Requirement | Where | Status |
|---|---|---|
| (a) Normalise dates to ISO 8601 | `services/cleaning.py::_parse_date` (3 formats) | ✅ |
| (a) Strip currency symbols from amounts | `cleaning.py::_parse_amount` (`$`, commas) | ✅ |
| (a) Upper-case status | `cleaning.py::_normalise_status` | ✅ |
| (a) Fill missing categories with 'Uncategorised' | `cleaning.py::clean_rows` + `constants.UNCATEGORISED` | ✅ |
| (a) Remove exact duplicate rows | `cleaning.py::clean_rows` (dedup on normalised tuple) | ✅ |
| Calculate `row_count_clean` (and raw) | `cleaning.py::clean_rows` returns both; persisted on Job | ✅ |
| (b) Outlier: amount > 3× account median | `services/anomaly.py::detect_anomalies` (Rule 1) | ✅ |
| (b) USD on domestic-only merchant | `anomaly.py` (Rule 2); `constants.DOMESTIC_ONLY_MERCHANTS` | ✅ |
| Store `is_anomaly`, `anomaly_reason` | `models/transaction.py`; written in `services/persistence.py` | ✅ |
| (c) LLM classify missing categories into the 8 cats | `services/classifier.py`; `constants.ALLOWED_CATEGORIES` | ✅ |
| (c) Batch LLM calls (not one per row) | `classifier.py::_batches` (`LLM_BATCH_SIZE`) | ✅ |
| (d) Single LLM summary call (totals, top-3, anomaly count, narrative, risk) | `services/summary.py::build_summary` | ✅ |
| (d) Store summary as structured data | `models/summary.py` (`JobSummary`, JSONB fields) | ✅ |
| (e) Retry LLM 3× with exponential backoff | `app/llm/retry.py::with_retries`; `LLM_MAX_RETRIES`/`LLM_RETRY_BASE_DELAY` | ✅ |
| (e) On failure mark `llm_failed`, **don't fail the job** | `classifier.py` (per-batch) / `summary.py` (null narrative) | ✅ verified |

## Data Model
| Requirement | Where | Status |
|---|---|---|
| `Job` (id, filename, status, row_count_raw/clean, created/completed_at, error_message) | `models/job.py` | ✅ |
| `Transaction` (+ is_anomaly, anomaly_reason, llm_category, llm_failed) | `models/transaction.py` | ✅ |
| `JobSummary` (totals, top_merchants, breakdown, anomaly_count, narrative, risk_level) | `models/summary.py` | ✅ |
| Relationships (Job 1–N Transaction, 1–1 JobSummary, cascade) | `models/*.py` `relationship(...)` | ✅ |
| Migrations | `alembic/versions/0001_initial_models.py` (verified: zero autogenerate drift) | ✅ |

## Cross-cutting / Quality
| Requirement | Where | Status |
|---|---|---|
| Async processing through the queue | `job_service` enqueues; `tasks.process_job` consumes | ✅ verified cross-process |
| Idempotency / transaction safety | `tasks.py` Phase A `FOR UPDATE` guard; `persistence.py` atomic delete-then-insert | ✅ verified |
| Graceful Gemini failures | retry → `llm_failed`; works with **no API key** | ✅ verified |
| Logging | `core/logging.py`; `job_id` in every pipeline log line | ✅ |
| Health checks | `routes_health.py` + compose healthchecks on all 4 services | ✅ verified |
| README (setup + curl) | `README.md` | ✅ |
| 3-min technical video script | `DEMO_SCRIPT.md` | ✅ |
| Scalability analysis | `DESIGN.md` §11 + `DEMO_SCRIPT.md` "Scale" | ✅ |

## Verified by integration tests (real Postgres + Redis)
- `transactions.csv`: raw **95** → clean **85** (10 duplicates removed).
- **10 anomalies**: 5 USD-domestic (Rule 2) + 5 amount-outliers (Rule 1).
- **13** uncategorised rows classified (fake-Gemini path); `llm_failed=0`.
- Graceful path (no key): job completes, `llm_failed=13`, narrative/risk null.
- Idempotent re-run: returns `skipped`, no duplicate rows.
- `docker compose up --build`: all four services healthy; full upload→results flow.

## Deliberate, documented decisions
- **Domestic-merchant set includes Zomato** alongside Swiggy/Ola/IRCTC — the brief's
  list is illustrative ("such as"), and Zomato is the merchant actually billed in USD
  in the dataset, so Rule 2 is demonstrable. One-line constant.
- **"Spend" = SUCCESS transactions only**, INR/USD reported separately (no FX). One-line
  filter in `summary.py` if all statuses are wanted.
