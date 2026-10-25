"""Async SQLAlchemy engine and session management."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import get_settings

settings = get_settings()

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # 'prefer' upgrades to TLS when the server supports it (Supabase pooler)
    # and stays plain for local dev Postgres without SSL configured.
    connect_args={"ssl": "prefer"},
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def create_all_tables() -> None:
    """Dev convenience: create any missing tables on startup.

    Resilient per-table creation so a missing extension (e.g. pgvector on a
    plain Postgres) only disables the RAG knowledge base, never the whole app.
    Alembic remains the source of truth for migrations; see `alembic/`.
    """
    from loguru import logger
    from sqlalchemy import text

    from app.models import Base

    async with engine.begin() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"pgvector unavailable ({exc}) — RAG anticipation falls back to keyword search"
            )
        for table in Base.metadata.sorted_tables:
            try:
                await conn.run_sync(
                    lambda sync_conn, t=table: t.create(sync_conn, checkfirst=True)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Table creation skipped for {table.name}: {exc}")
