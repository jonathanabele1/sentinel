"""Fixtures for integration tests that need a live Postgres.

Prerequisites: `make up && make migrate` must have run so the database
exists and has the schema. Each test gets a session against the same DB
the dev app uses; tests are responsible for cleaning up rows they create.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from apps.api.config import get_settings
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession bound to the dev/test Postgres.

    The session is closed at the end of each test. The engine is disposed
    too so we don't leak connections across the test run.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()
