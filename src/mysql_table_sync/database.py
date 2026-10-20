"""Database engine construction and lifecycle management."""

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine

from mysql_table_sync.config import Settings


def create_engines(settings: Settings) -> tuple[Engine, Engine]:
    """Create source and target engines without opening a connection yet."""
    options = {"pool_pre_ping": True, "pool_recycle": 1800}
    return (
        create_engine(settings.source_database_url, **options),
        create_engine(settings.target_database_url, **options),
    )


@contextmanager
def engine_pair(settings: Settings) -> Iterator[tuple[Engine, Engine]]:
    """Yield engines and reliably dispose their connection pools afterwards."""
    source_engine, target_engine = create_engines(settings)
    try:
        yield source_engine, target_engine
    finally:
        source_engine.dispose()
        target_engine.dispose()
