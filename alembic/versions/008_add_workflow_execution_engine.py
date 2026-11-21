"""Add durable workflow steps, run snapshots, and bound approvals."""

import sqlalchemy as sa
from alembic import op

from devflow_api.models.base import UTCDateTime

revision = "008_add_workflow_execution_engine"
down_revision = "007_add_github_confirmations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_step_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_key", sa.Text(), nullable=False),
        sa.Column("step_version", sa.Integer(), nullable=False),
        sa.Column("executor_id", sa.Text(), nullable=False),
        sa.Column("tool_selection_json", sa.Text()),
        sa.Column("depends_on_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("inputs_json", sa.Text()),
        sa.Column("input_digest", sa.Text()),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_status", sa.Text()),
        sa.Column("output_json", sa.Text()),
        sa.Column("artifact_refs_json", sa.Text()),
        sa.Column("error_json", sa.Text()),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_expires_at", UTCDateTime()),
        sa.Column("ready_at", UTCDateTime()),
        sa.Column("dispatched_at", UTCDateTime()),
        sa.Column("started_at", UTCDateTime()),
        sa.Column("completed_at", UTCDateTime()),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','READY','RUNNING','WAITING_APPROVAL',"
            "'SUCCEEDED','FAILED','CANCELLED','INTERRUPTED')"
        ),
        sa.UniqueConstraint("workflow_run_id", "step_key", name="uq_step_run_key"),
        sqlite_autoincrement=True,
    )
    for name, column in (
        ("idx_step_runs_run_id", "workflow_run_id"),
        ("idx_step_runs_status", "status"),
        ("idx_step_runs_lease_expiry", "lease_expires_at"),
    ):
        op.create_index(name, "workflow_step_runs", [column])
    for name, kind in (
        ("template_snapshot_json", sa.Text()),
        ("template_version", sa.Integer()),
        ("template_hash", sa.Text()),
    ):
        op.add_column("workflow_runs", sa.Column(name, kind))
    op.create_index("idx_workflow_runs_template_hash", "workflow_runs", ["template_hash"])
    for name in ("operation", "parameters_hash", "actor_id"):
        op.add_column("approval_requests", sa.Column(name, sa.Text()))
    # A table-level FK round-trips through SQLAlchemy's SQLite reflection reliably.
    sequence = op.get_bind().scalar(
        sa.text("SELECT seq FROM sqlite_sequence WHERE name='approval_requests'")
    )
    with op.batch_alter_table(
        "approval_requests",
        table_args=(sa.CheckConstraint("status IN ('pending','approved','rejected','expired')"),),
        table_kwargs={"sqlite_autoincrement": True},
    ) as batch:
        batch.add_column(sa.Column("workflow_step_run_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_approval_step_run",
            "workflow_step_runs",
            ["workflow_step_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
    _restore_approval_sequence(sequence)
    for name in ("consumed_at", "expires_at", "authorization_expires_at"):
        op.add_column("approval_requests", sa.Column(name, UTCDateTime()))
    for name, column in (
        ("idx_approval_operation", "operation"),
        ("idx_approval_consumed_at", "consumed_at"),
        ("idx_approval_step_run_id", "workflow_step_run_id"),
        ("idx_approval_expires_at", "expires_at"),
        ("idx_approval_authz_expires_at", "authorization_expires_at"),
    ):
        op.create_index(name, "approval_requests", [column])
    op.create_index(
        "uidx_approval_step_pending",
        "approval_requests",
        ["workflow_step_run_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending' AND operation IS NOT NULL"),
    )


def downgrade() -> None:
    for name in (
        "uidx_approval_step_pending",
        "idx_approval_operation",
        "idx_approval_consumed_at",
        "idx_approval_step_run_id",
        "idx_approval_expires_at",
        "idx_approval_authz_expires_at",
    ):
        op.drop_index(name, table_name="approval_requests")
    sequence = op.get_bind().scalar(
        sa.text("SELECT seq FROM sqlite_sequence WHERE name='approval_requests'")
    )
    with op.batch_alter_table(
        "approval_requests",
        table_args=(sa.CheckConstraint("status IN ('pending','approved','rejected','expired')"),),
        table_kwargs={"sqlite_autoincrement": True},
    ) as batch:
        batch.drop_constraint("fk_approval_step_run", type_="foreignkey")
        for name in (
            "operation",
            "parameters_hash",
            "actor_id",
            "workflow_step_run_id",
            "consumed_at",
            "expires_at",
            "authorization_expires_at",
        ):
            batch.drop_column(name)
    _restore_approval_sequence(sequence)
    op.drop_index("idx_workflow_runs_template_hash", table_name="workflow_runs")
    for name in ("template_snapshot_json", "template_version", "template_hash"):
        op.drop_column("workflow_runs", name)
    op.drop_table("workflow_step_runs")


def _restore_approval_sequence(sequence: int | None) -> None:
    if sequence is not None:
        op.get_bind().execute(
            sa.text(
                "UPDATE sqlite_sequence SET seq = MAX(seq, :previous) "
                "WHERE name='approval_requests'"
            ),
            {"previous": sequence},
        )
