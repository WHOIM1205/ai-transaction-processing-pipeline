# 3–5 Minute Technical Demo Script

> Treat this as an engineering design review with your Tech Lead. Have the
> architecture diagram (README) and a terminal ready. Times are guidance.

---

## 0:00 – 0:30 — Framing
"This is an AI-powered transaction-processing pipeline. You upload a deliberately
dirty CSV of ~90 financial transactions; the system cleans it, flags anomalies,
uses Gemini to categorise and summarise, and serves a structured report via a
polling API. The whole thing comes up with a single `docker compose up`."

Show: `docker compose ps` — four healthy services: **api, worker, postgres, redis**.

---

## 0:30 – 1:15 — Architecture & the "why"
Point at the diagram:
- "**FastAPI** is a thin HTTP layer. On upload it validates the file, writes a
  `pending` **Job** to **Postgres**, drops the `job_id` onto **Redis**, and returns
  `202` immediately — so the request never blocks on processing."
- "A **Celery worker** consumes the job and runs the pipeline. The API and worker
  **share no memory** — they coordinate only through Postgres (the truth) and Redis
  (the work queue). The file is passed by reference via a shared volume — the
  claim-check pattern."
- "Layering is strict: `routes → services → repositories → models`. Business logic
  lives in `services/` and is unit-testable without HTTP, Celery, or a live LLM."

---

## 1:15 – 2:00 — Upload flow & async processing (live)
```bash
curl -s -X POST http://localhost:8000/jobs/upload -F "file=@transactions.csv"
```
"I get a `job_id` back instantly with status `pending`."
```bash
curl -s http://localhost:8000/jobs/<id>/status
```
"Poll the status — it transitions **pending → processing → completed** on its own,
driven by the worker." Show `docker compose logs worker`:
"Notice the worker **claims the job with `SELECT … FOR UPDATE`** — that's the
idempotency guard, so a duplicate delivery can't double-process."

---

## 2:00 – 2:40 — Cleaning & anomaly detection
"From `raw=95` rows we get `clean=85` — the cleaner normalised three date formats,
stripped `$` from amounts, upper-cased currency/status, filled blank categories,
and removed **10 exact duplicate rows**."

Show two anomaly rows from the results:
- "**Rule 1 — statistical outlier:** `TXN2003`, IRCTC ₹193,647 — over 3× its
  account's median, so it's flagged `amount_gt_3x_account_median`."
- "**Rule 2 — currency mismatch:** Zomato charged in **USD** — a domestic-only brand
  shouldn't be, so `usd_on_domestic_merchant`."
"A row can trip both rules, so reasons are an array."

---

## 2:40 – 3:20 — Gemini classification & summary
"For rows with **no category**, the worker batches them — never one call per row —
and asks **Gemini 1.5 Flash** to classify each into a fixed set of 8 categories,
returning structured JSON. Out-of-set answers are coerced to `Other`, so a bad model
response can never reach the database."

"Then a **single** Gemini call turns the pre-computed aggregates into a 2–3 sentence
narrative and a risk level. Crucially, **we compute the numbers ourselves** — the LLM
only writes prose and judges risk."

```bash
curl -s "http://localhost:8000/jobs/<id>/results?limit=20" | jq .summary
```
Show `total_spend_inr/usd`, `top_merchants`, `category_breakdown`, `anomaly_count`,
`risk_level`, `narrative`.

---

## 3:20 – 4:00 — Resilience (the part reviewers care about)
"Gemini is the one external dependency, so it can **never** fail the job:
- Each call **retries up to 3× with exponential backoff**.
- If a batch still fails, those rows are marked `llm_failed` and the job continues.
- With **no API key at all**, classification is skipped, the narrative/risk are left
  null — but cleaning, anomalies, and the deterministic aggregates still produce a
  complete report."

"Persistence is **atomic** — transactions, summary, and the status flip commit in one
transaction, so a partial result is never observable; and re-running replaces prior
results rather than duplicating them."

---

## 4:00 – 4:45 — Scale (the breaking point)
"At 100× traffic, the first three things to break are:
1. **Gemini rate limits** — fix with a dedicated rate-limited LLM queue and caching
   by merchant.
2. **The single worker / serial pipeline** — scale workers horizontally and split
   classification into parallel subtasks via a Celery chord.
3. **Whole-file-in-memory parsing on a local volume** — stream uploads from object
   storage (S3) instead.
Plus PgBouncer for connection pooling and caching the status endpoint in Redis to
absorb polling."

---

## 4:45 – 5:00 — Close
"So: a clean, layered, single-command system that's correct on the happy path and
degrades gracefully when the LLM isn't there — with a clear path to enterprise scale.
Every pipeline stage is an isolated, testable service, and the whole flow is covered
by integration tests against real Postgres and Redis."
