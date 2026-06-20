"""initial models: jobs, transactions, job_summaries

Revision ID: 0001_initial_models
Revises:
Create Date: 2026-06-20

Baseline schema. Creates the four PostgreSQL enum types and the three tables
(jobs, transactions, job_summaries) with their relationships, indexes, and
constraints — mirroring the SQLAlchemy models exactly so future
`--autogenerate` runs produce empty diffs.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_models"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- jobs ----------------------------------------------------------------
    # The Enum type is created automatically by create_table on first use.
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "processing", "completed", "failed", name="job_status"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("row_count_raw", sa.Integer(), nullable=True),
        sa.Column("row_count_clean", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])

    # --- transactions --------------------------------------------------------
    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("txn_id", sa.String(length=64), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("merchant", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "currency", sa.Enum("INR", "USD", name="currency"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum("SUCCESS", "FAILED", "PENDING", name="transaction_status"),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.String(length=64),
            server_default="Uncategorised",
            nullable=False,
        ),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_anomaly",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("anomaly_reason", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("llm_category", sa.String(length=64), nullable=True),
        sa.Column(
            "llm_failed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.CheckConstraint("amount >= 0", name="ck_transactions_amount_non_negative"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_transactions_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
    )
    op.create_index("ix_transactions_job_id", "transactions", ["job_id"])
    op.create_index(
        "ix_transactions_job_id_account_id",
        "transactions",
        ["job_id", "account_id"],
    )
    # Partial index — only flagged rows, for fast anomaly retrieval.
    op.create_index(
        "ix_transactions_job_id_is_anomaly",
        "transactions",
        ["job_id"],
        postgresql_where=sa.text("is_anomaly"),
    )

    # --- job_summaries -------------------------------------------------------
    op.create_table(
        "job_summaries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "total_spend_inr",
            sa.Numeric(precision=16, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_spend_usd",
            sa.Numeric(precision=16, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "top_merchants",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "category_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "anomaly_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column(
            "risk_level",
            sa.Enum("low", "medium", "high", name="risk_level"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_summaries_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_summaries"),
        sa.UniqueConstraint("job_id", name="uq_job_summaries_job_id"),
    )


def downgrade() -> None:
    # Drop tables (children first), then the enum types they created.
    op.drop_table("job_summaries")
    op.drop_index("ix_transactions_job_id_is_anomaly", table_name="transactions")
    op.drop_index("ix_transactions_job_id_account_id", table_name="transactions")
    op.drop_index("ix_transactions_job_id", table_name="transactions")
    op.drop_table("transactions")
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")

    # Enum types are not dropped by drop_table — remove them explicitly.
    bind = op.get_bind()
    for enum_name in ("risk_level", "transaction_status", "currency", "job_status"):
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
