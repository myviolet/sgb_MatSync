"""Command-line interface."""

import argparse
import logging
import sys
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from mysql_table_sync.config import Settings
from mysql_table_sync.database import engine_pair
from mysql_table_sync.exceptions import SyncError
from mysql_table_sync.logging_config import configure_logging
from mysql_table_sync.service import TableSyncService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mysql-table-sync",
        description="Scaffold for syncing SAP_RW_Material_Move to record_frozen_data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="check connectivity to both configured databases")
    subparsers.add_parser("sync", help="run initial full sync or continue from the saved source ID")
    return parser


def _check_connections(settings: Settings) -> None:
    with engine_pair(settings) as (source_engine, target_engine):
        with source_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        with target_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    print("Both database connections are available.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        configure_logging(settings)
        logger = logging.getLogger(__name__)
        if args.command == "check":
            _check_connections(settings)
            logger.info("Database connectivity check succeeded")
            return 0
        if args.command == "sync":
            logger.info(
                "Starting sync source_table=%s target_table=%s batch_size=%s",
                settings.source_table,
                settings.target_table,
                settings.batch_size,
            )
            with engine_pair(settings) as (source_engine, target_engine):
                report = TableSyncService(source_engine, target_engine, settings).run()
            logger.info(
                "Sync completed source_table=%s target_table=%s rows_read=%s rows_written=%s",
                report.source_table,
                report.target_table,
                report.rows_read,
                report.rows_written,
            )
            return 0
    except (ValueError, SyncError, SQLAlchemyError) as error:
        if logging.getLogger().handlers:
            logging.getLogger(__name__).error("Command failed: %s", error)
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
