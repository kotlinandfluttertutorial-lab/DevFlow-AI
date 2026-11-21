"""Persistent workflow definitions and run state."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from devflow_api.models.base import Base, TimestampMixin, UTCDateTime


class Workflow(TimestampMixin, Base):
    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint("status IN ('draft','active','paused','completed','failed')"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    definition_json: Mapped[str | None] = mapped_column(
        Text, deferred=True, deferred_raiseload=True
    )


class WorkflowRun(TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','waiting_approval','completed','failed','cancelled')"
        ),
        Index("idx_workflow_runs_template_hash", "template_hash"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    result_json: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    template_snapshot_json: Mapped[str | None] = mapped_column(Text)
    template_version: Mapped[int | None] = mapped_column()
    template_hash: Mapped[str | None] = mapped_column(Text)
