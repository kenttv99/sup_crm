from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import async_sessionmaker as sqlalchemy_async_sessionmaker
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_sync_engine: Optional[Engine] = None
_async_engine: Optional[AsyncEngine] = None
_async_sessionmaker: Optional[sqlalchemy_async_sessionmaker[AsyncSession]] = None


def _settings() -> Any:
    from config.settings import get_settings

    return get_settings()


def get_sync_database_url() -> str:
    return _settings().sync_database_url


def get_async_database_url() -> str:
    return _settings().async_database_url


def get_sql_echo() -> bool:
    return _settings().sql_echo


def get_sync_engine() -> Engine:
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(
            get_sync_database_url(),
            pool_pre_ping=True,
            echo=get_sql_echo(),
        )
    return _sync_engine


def get_async_engine() -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(
            get_async_database_url(),
            pool_pre_ping=True,
            echo=get_sql_echo(),
        )
    return _async_engine


def get_async_sessionmaker() -> sqlalchemy_async_sessionmaker[AsyncSession]:
    global _async_sessionmaker
    if _async_sessionmaker is None:
        _async_sessionmaker = sqlalchemy_async_sessionmaker(
            bind=get_async_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_sessionmaker


class _LazySyncEngine:
    @property
    def url(self) -> URL:
        return get_sync_engine().url

    def connect(self, *args: Any, **kwargs: Any) -> Any:
        return get_sync_engine().connect(*args, **kwargs)

    def begin(self, *args: Any, **kwargs: Any) -> Any:
        return get_sync_engine().begin(*args, **kwargs)

    def dispose(self, *args: Any, **kwargs: Any) -> Any:
        return get_sync_engine().dispose(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(get_sync_engine(), name)


class _LazyAsyncEngine:
    def connect(self, *args: Any, **kwargs: Any) -> Any:
        return get_async_engine().connect(*args, **kwargs)

    def begin(self, *args: Any, **kwargs: Any) -> Any:
        return get_async_engine().begin(*args, **kwargs)

    async def dispose(self, *args: Any, **kwargs: Any) -> Any:
        return await get_async_engine().dispose(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(get_async_engine(), name)


class _LazyAsyncSessionmaker:
    def __call__(self, *args: Any, **kwargs: Any) -> AsyncSession:
        return get_async_sessionmaker()(*args, **kwargs)

    def configure(self, *args: Any, **kwargs: Any) -> None:
        get_async_sessionmaker().configure(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(get_async_sessionmaker(), name)


sync_engine = _LazySyncEngine()
async_engine = _LazyAsyncEngine()
async_sessionmaker = _LazyAsyncSessionmaker()
async_session_maker = async_sessionmaker
AsyncSessionLocal = async_sessionmaker
