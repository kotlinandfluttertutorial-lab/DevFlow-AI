"""Durable step lifecycle and fenced execution leases."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from devflow_api.models.base import Base, TimestampMixin, UTCDateTime


class WorkflowStepRun(TimestampMixin, Base):
    __tablename__ = "workflow_step_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','READY','RUNNING','WAITING_APPROVAL',"
            "'SUCCEEDED','FAILED','CANCELLED','INTERRUPTED')"
        ),
        UniqueConstraint("workflow_run_id", "step_key", name="uq_step_run_key"),
        Index("idx_step_runs_run_id", "workflow_run_id"),
        Index("idx_step_runs_status", "status"),
        Index("idx_step_runs_lease_expiry", "lease_expires_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"))
    step_key: Mapped[str] = mapped_column(Text)
    step_version: Mapped[int] = mapped_column(Integer)
    executor_id: Mapped[str] = mapped_column(Text)
    tool_selection_json: Mapped[str | None] = mapped_column(Text)
    depends_on_json: Mapped[str] = mapped_column(Text, server_default="[]")
    inputs_json: Mapped[str | None] = mapped_column(Text)
    input_digest: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, server_default="0")
    result_status: Mapped[str | None] = mapped_column(Text)
    output_json: Mapped[str | None] = mapped_column(Text)
    artifact_refs_json: Mapped[str | None] = mapped_column(Text)
    error_json: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ready_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
