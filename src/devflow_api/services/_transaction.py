"""Transaction boundaries for operations spanning repositories."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


def shared_session(session: AsyncSession, *others: AsyncSession) -> AsyncSession:
    if any(other is not session for other in others):
        raise ValueError("Service repositories must share the same session")
    return session


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncIterator[None]:
    """Commit the current unit of work, including sessions that already autobegan."""
    if session.info.get("workflow_immediate"):
        try:
            yield
        except BaseException:
            session.info["workflow_rollback_only"] = True
            raise
        return
    try:
        yield
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
