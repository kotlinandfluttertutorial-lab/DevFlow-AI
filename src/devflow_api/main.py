"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from devflow_api.adapters.live.figma import FigmaAdapter
from devflow_api.adapters.live.github import GitHubAdapter
from devflow_api.adapters.live.mcp_live import MCPLiveAdapter
from devflow_api.adapters.live.ollama import OllamaAdapter
from devflow_api.adapters.registry import AdapterRegistry, registry
from devflow_api.database import _configure_sqlite, get_db
from devflow_api.middleware.error_handler import add_error_handlers
from devflow_api.middleware.logging import RequestLoggingMiddleware
from devflow_api.routers.approvals import router as approvals_router
from devflow_api.routers.health import router as health_router
from devflow_api.routers.integrations import router as integrations_router
from devflow_api.routers.learning import router as learning_router
from devflow_api.routers.projects import router as projects_router
from devflow_api.routers.repositories import github_outcome_error
from devflow_api.routers.repositories import router as repositories_router
from devflow_api.routers.workflows import router as workflows_router
from devflow_api.services.recovery import RecoveryService
from devflow_api.services.repository import GitHubOutcomeError
from devflow_core.config import Settings, get_settings
from devflow_core.exceptions import ConfigurationError
from devflow_core.logging import configure_logging
from devflow_core.mcp_config import MCPServerConfig


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else get_settings()
    configure_logging(config.LOG_LEVEL, config.LOG_FORMAT)
    app_engine = create_async_engine(config.DATABASE_URL, hide_parameters=True)
    event.listen(app_engine.sync_engine, "connect", _configure_sqlite)
    sessions = async_sessionmaker(app_engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with app_engine.connect() as connection:
                ready = await connection.run_sync(
                    lambda sync: inspect(sync).has_table("workflow_step_runs")
                )
            if not ready:
                raise ConfigurationError("Apply Alembic migration 008 before starting the API")
            async with sessions() as session:
                app.state.recovered_steps = await RecoveryService(session, config).recover()
            yield
        finally:
            await app_engine.dispose()

    async def application_db() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app = FastAPI(
        title=config.APP_NAME,
        version=config.APP_VERSION,
        description="Local-first AI development and learning orchestrator.",
        lifespan=lifespan,
    )
    app.dependency_overrides[get_db] = application_db
    app.state.workflow_sessions = sessions
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)
    add_error_handlers(app)
    app.add_exception_handler(GitHubOutcomeError, github_outcome_error)
    app.state.settings = config
    app_registry = AdapterRegistry()
    for name in registry.list_names():
        app_registry.register(registry.get(name))
    if config.GITHUB_TOKEN:
        app_registry.register_live("github", GitHubAdapter(config))
    if config.FIGMA_TOKEN:
        app_registry.register_live("figma", FigmaAdapter(config))
    app_registry.register_live("ollama", OllamaAdapter(config))
    if any(server.enabled for server in MCPServerConfig.from_settings(config)):
        app_registry.register_live("mcp", MCPLiveAdapter(config))
    app.state.adapter_registry = app_registry
    app.include_router(health_router)
    app.include_router(projects_router)
    app.include_router(learning_router)
    app.include_router(integrations_router)
    app.include_router(workflows_router)
    app.include_router(approvals_router)
    app.include_router(repositories_router)
    return app


app = create_app()
