"""Shared FastAPI dependencies.

WHY THIS FILE EXISTS
--------------------
A single seam for cross-route dependencies so handlers stay thin and decoupled
from where things come from:
  * `get_db` — re-exported from the db layer so routes depend on `api.deps`
    rather than reaching into `app.db.session` directly.
  * `PaginationParams` — reusable, validated limit/offset for list endpoints.
"""

from dataclasses import dataclass

from fastapi import Query

from app.db.session import get_db  # re-exported for use as a route dependency

__all__ = ["get_db", "PaginationParams"]


@dataclass
class PaginationParams:
    """Validated pagination, shared by any list endpoint.

    `limit` is capped at 100 to bound response size and DB work; `offset` is
    non-negative. FastAPI validates the bounds and documents them in OpenAPI.
    """

    limit: int = Query(50, ge=1, le=100, description="Max items to return (1–100).")
    offset: int = Query(0, ge=0, description="Number of items to skip.")
