"""Safe, consistent JSON errors for the HTTP API."""

from http import HTTPStatus
from traceback import extract_tb

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException

from devflow_api.adapters.exceptions import (
    IntegrationExecutionError,
    MCPApprovalExpiredError,
    MCPApprovalRequiredError,
    MCPPermissionError,
    MCPServerNotFoundError,
    MCPTimeoutError,
    MissingCredentialError,
)
from devflow_api.schemas.common import ErrorResponse
from devflow_core.exceptions import (
    ApprovalAlreadyResolvedError,
    ApprovalExpiredError,
    ApprovalNotConsumableError,
    ConfirmationAlreadyUsedError,
    ConfirmationExpiredError,
    ConfirmationMismatchError,
    ConfirmationNotFoundError,
    ConfirmationRequiredError,
    DatabaseBusyError,
    InvalidStatusTransitionError,
    InvalidStepTransitionError,
    InvalidWorkflowTemplateError,
    OperationNotPermitted,
    RunNotStartableError,
    StepDispatchConflict,
)
from devflow_core.logging import get_logger

_DOMAIN_ERRORS: dict[type[Exception], tuple[int, str, str]] = {
    InvalidWorkflowTemplateError: (422, "Invalid workflow template", "INVALID_WORKFLOW_TEMPLATE"),
    InvalidStepTransitionError: (409, "Invalid step transition", "INVALID_STEP_TRANSITION"),
    RunNotStartableError: (409, "Run cannot be started", "RUN_NOT_STARTABLE"),
    StepDispatchConflict: (409, "Workflow dispatch conflict", "STEP_DISPATCH_CONFLICT"),
    ApprovalNotConsumableError: (409, "Approval cannot be consumed", "APPROVAL_NOT_CONSUMABLE"),
    DatabaseBusyError: (503, "Workflow database is busy", "DATABASE_BUSY"),
    ApprovalAlreadyResolvedError: (409, "Approval already resolved", "APPROVAL_ALREADY_RESOLVED"),
    ApprovalExpiredError: (409, "Approval expired", "APPROVAL_EXPIRED"),
    OperationNotPermitted: (409, "Operation not permitted", "OPERATION_NOT_PERMITTED"),
    InvalidStatusTransitionError: (422, "Invalid status transition", "INVALID_STATUS_TRANSITION"),
    IntegrationExecutionError: (502, "Integration failed", "INTEGRATION_ERROR"),
    MissingCredentialError: (403, "Credential required", "MISSING_CREDENTIAL"),
    ConfirmationRequiredError: (422, "Confirmation required", "CONFIRMATION_REQUIRED"),
    ConfirmationExpiredError: (422, "Confirmation expired", "CONFIRMATION_EXPIRED"),
    ConfirmationMismatchError: (422, "Confirmation mismatch", "CONFIRMATION_MISMATCH"),
    ConfirmationAlreadyUsedError: (409, "Confirmation already used", "CONFIRMATION_ALREADY_USED"),
    ConfirmationNotFoundError: (404, "Confirmation not found", "CONFIRMATION_NOT_FOUND"),
    MCPServerNotFoundError: (404, "MCP server not found", "MCP_SERVER_NOT_FOUND"),
    MCPPermissionError: (403, "MCP permission denied", "MCP_PERMISSION_DENIED"),
    MCPTimeoutError: (504, "MCP request timed out", "MCP_TIMEOUT"),
    MCPApprovalRequiredError: (403, "MCP approval required", "MCP_APPROVAL_REQUIRED"),
    MCPApprovalExpiredError: (403, "MCP approval expired", "MCP_APPROVAL_EXPIRED"),
}


def _error(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=ErrorResponse(error=message, code=code).model_dump()
    )


async def _http_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, HTTPException):
        raise exc
    if not is_body_allowed_for_status_code(exc.status_code):
        return Response(status_code=exc.status_code, headers=exc.headers)
    try:
        status = HTTPStatus(exc.status_code)
    except ValueError:
        message, code = "HTTP error", "HTTP_ERROR"
    else:
        message, code = status.phrase, status.name
    if exc.status_code == 404:
        message = "Not found"
    response = _error(exc.status_code, message, code)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    return _error(422, "Validation error", "VALIDATION_ERROR")


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    for error_type in type(exc).__mro__:
        mapping = _DOMAIN_ERRORS.get(error_type)
        if mapping is not None:
            return _error(*mapping)
    return await handle_unexpected_error(request, exc)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    frames = extract_tb(exc.__traceback__)
    get_logger(__name__).error(
        "Unhandled request exception",
        request_id=getattr(request.state, "request_id", None),
        exception_type=type(exc).__name__,
        traceback=[f"{frame.name}:{frame.lineno}" for frame in frames],
    )
    return _error(500, "Internal server error", "INTERNAL_ERROR")


def add_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    for error_type in _DOMAIN_ERRORS:
        app.add_exception_handler(error_type, _domain_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
