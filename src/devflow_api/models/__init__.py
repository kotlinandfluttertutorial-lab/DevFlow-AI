"""Register application models in the shared metadata."""

from devflow_api.models.approval import ApprovalRequest
from devflow_api.models.audit import WorkflowAuditLog
from devflow_api.models.base import Base
from devflow_api.models.github_confirmation import GitHubConfirmation
from devflow_api.models.integration import IntegrationConfig
from devflow_api.models.learning import LearningItem
from devflow_api.models.mcp_approval import MCPApproval
from devflow_api.models.project import Project
from devflow_api.models.repository import RepositoryConfig
from devflow_api.models.workflow import Workflow, WorkflowRun
from devflow_api.models.workflow_step_run import WorkflowStepRun

__all__ = [
    "ApprovalRequest",
    "Base",
    "GitHubConfirmation",
    "IntegrationConfig",
    "LearningItem",
    "MCPApproval",
    "Project",
    "RepositoryConfig",
    "Workflow",
    "WorkflowAuditLog",
    "WorkflowRun",
    "WorkflowStepRun",
]
