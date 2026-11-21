"""Composable, explicit SQLite write transactions for the execution repositories."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import SessionTransactionOrigin


@asynccontextmanager
async def immediate_transaction(
    session: AsyncSession, *, commit: bool = True
) -> AsyncIterator[None]:
    """Join our outer transaction or own a fresh transaction, never commit caller work."""
    if session.info.get("workflow_immediate"):
        if commit:
            raise InvalidRequestError("Nested workflow writes must explicitly use commit=False")
        try:
            yield
        except BaseException:
            session.info["workflow_rollback_only"] = True
            raise
        return
    if not commit:
        raise InvalidRequestError("commit=False requires an outer immediate_transaction")
    if session.in_transaction():
        transaction = session.sync_session.get_transaction()
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        driver = raw.driver_connection
        if (
            transaction is None
            or transaction.origin != SessionTransactionOrigin.AUTOBEGIN
            or session.new
            or session.dirty
            or session.deleted
            or driver is None
            or driver.in_transaction
        ):
            raise InvalidRequestError(
                "Finish the existing transaction before starting workflow writes"
            )
    try:
        await session.execute(text("BEGIN IMMEDIATE"))
        session.info["workflow_immediate"] = True
        yield
        if session.info.get("workflow_rollback_only"):
            raise InvalidRequestError(
                "A nested workflow operation failed; the unit of work rolled back"
            )
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        session.info.pop("workflow_immediate", None)
        session.info.pop("workflow_rollback_only", None)
