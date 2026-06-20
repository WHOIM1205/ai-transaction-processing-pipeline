"""API endpoint tests (HTTP layer, external services stubbed)."""

import datetime
import uuid
from types import SimpleNamespace

from app.models.enums import JobStatus
from app.repositories import job_repo, transaction_repo
from app.services import job_service
from app.services.errors import InvalidFileExtension

CSV_BYTES = (
    b"txn_id,date,merchant,amount,currency,status,category,account_id,notes\n"
    b"TXN1,04-09-2024,Flipkart,100.00,INR,SUCCESS,Shopping,ACC1,\n"
)


def _fake_job(status=JobStatus.completed):
    """A stand-in for the ORM Job, exposing the attributes the schemas read."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        filename="transactions.csv",
        status=status,
        row_count_raw=1,
        row_count_clean=1,
        error_message=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        completed_at=datetime.datetime.now(datetime.timezone.utc),
        summary=None,
    )


# --- upload ----------------------------------------------------------------
def test_upload_returns_202_with_job_id(client, monkeypatch):
    job = _fake_job(status=JobStatus.pending)
    monkeypatch.setattr(job_service, "create_job_from_upload", lambda db, fn, c: job)

    resp = client.post(
        "/jobs/upload", files={"file": ("transactions.csv", CSV_BYTES, "text/csv")}
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == str(job.id)
    assert body["status"] == "pending"


def test_upload_invalid_file_maps_to_400(client, monkeypatch):
    def _raise(db, fn, c):
        raise InvalidFileExtension("Only .csv files are accepted.")

    monkeypatch.setattr(job_service, "create_job_from_upload", _raise)
    resp = client.post(
        "/jobs/upload", files={"file": ("data.txt", b"x", "text/plain")}
    )
    assert resp.status_code == 400
    assert "Only .csv" in resp.json()["detail"]


# --- status ----------------------------------------------------------------
def test_status_returns_job(client, monkeypatch):
    job = _fake_job(status=JobStatus.completed)
    monkeypatch.setattr(job_repo, "get_job", lambda db, jid: job)

    resp = client.get(f"/jobs/{job.id}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == str(job.id)
    assert body["status"] == "completed"
    assert body["row_count_clean"] == 1


def test_status_unknown_job_returns_404(client, monkeypatch):
    monkeypatch.setattr(job_repo, "get_job", lambda db, jid: None)
    resp = client.get(f"/jobs/{uuid.uuid4()}/status")
    assert resp.status_code == 404


# --- results ---------------------------------------------------------------
def test_results_409_when_not_completed(client, monkeypatch):
    job = _fake_job(status=JobStatus.processing)
    monkeypatch.setattr(job_repo, "get_job", lambda db, jid: job)

    resp = client.get(f"/jobs/{job.id}/results")
    assert resp.status_code == 409
    assert "not completed" in resp.json()["detail"].lower()


def test_results_200_with_paginated_transactions(client, monkeypatch):
    job = _fake_job(status=JobStatus.completed)
    monkeypatch.setattr(job_repo, "get_job", lambda db, jid: job)
    monkeypatch.setattr(transaction_repo, "list_for_job", lambda db, jid, limit, offset: ([], 0))

    resp = client.get(f"/jobs/{job.id}/results?limit=5&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == str(job.id)
    assert body["status"] == "completed"
    assert body["transactions"] == {"items": [], "total": 0, "limit": 5, "offset": 0}


def test_results_unknown_job_returns_404(client, monkeypatch):
    monkeypatch.setattr(job_repo, "get_job", lambda db, jid: None)
    resp = client.get(f"/jobs/{uuid.uuid4()}/results")
    assert resp.status_code == 404
