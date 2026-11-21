"""Workflow step approval persistence."""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from devflow_api.models.base import Base, TimestampMixin, UTCDateTime


class ApprovalRequest(TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected','expired')"),
        Index("idx_approval_operation", "operation"),
        Index("idx_approval_consumed_at", "consumed_at"),
        Index("idx_approval_step_run_id", "workflow_step_run_id"),
        Index("idx_approval_expires_at", "expires_at"),
        Index("idx_approval_authz_expires_at", "authorization_expires_at"),
        Index(
            "uidx_approval_step_pending",
            "workflow_step_run_id",
            unique=True,
            sqlite_where=text("status = 'pending' AND operation IS NOT NULL"),
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"))
    step_name: Mapped[str] = mapped_column(Text)
    action_description: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_by: Mapped[str | None] = mapped_column(Text)
    operation: Mapped[str | None] = mapped_column(Text)
    parameters_hash: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(Text)
    workflow_step_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_step_runs.id", ondelete="SET NULL")
    )
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    authorization_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    def __init__(self, **fields: object) -> None:
        requested_at = fields.setdefault("requested_at", datetime.now(UTC))
        fields.setdefault("created_at", requested_at)
        super().__init__(**fields)
