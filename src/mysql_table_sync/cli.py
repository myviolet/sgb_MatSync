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
from mysql_table_sync.sources import (
    PIG_IRON_SALES_PROFILE,
    PRODUCTIVITY_PROFILE,
    SyncProfile,
    material_move_profile,
)

SYNC_COMMANDS = ("sync", "sync-sales", "sync-productivity", "sync-all")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mysql-table-sync",
        description="Sync SAP source tables into record_frozen_data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="check connectivity to both configured databases")
    subparsers.add_parser(
        "sync", help="sync SAP_RW_Material_Move (initial full sync or continue from saved cursor)"
    )
    subparsers.add_parser("sync-sales", help="sync SAP_PG_Sales (pig iron sales)")
    subparsers.add_parser("sync-productivity", help="sync SAP_Productivity (finished products)")
    subparsers.add_parser("sync-all", help="run every sync profile sequentially")
    return parser


def _check_connections(settings: Settings) -> None:
    with engine_pair(settings) as (source_engine, target_engine):
        with source_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        with target_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    print("Both database connections are available.")


def _profiles_for(command: str, settings: Settings) -> list[SyncProfile]:
    profiles: list[SyncProfile] = []
    if command in ("sync", "sync-all"):
        profiles.append(material_move_profile(settings))
    if command in ("sync-sales", "sync-all"):
        profiles.append(PIG_IRON_SALES_PROFILE)
    if command in ("sync-productivity", "sync-all"):
        profiles.append(PRODUCTIVITY_PROFILE)
    return profiles


def _run_profiles(settings: Settings, profiles: Sequence[SyncProfile]) -> int:
    """Run every profile; one failure is reported but does not block the others."""
    failures: list[str] = []
    with engine_pair(settings) as (source_engine, target_engine):
        for profile in profiles:
            logger = logging.getLogger(__name__)
            logger.info(
                "Starting sync profile=%s source_table=%s target_table=%s batch_size=%s",
                profile.name,
                profile.source_table,
                settings.target_table,
                settings.batch_size,
            )
            try:
                report = TableSyncService(
                    source_engine, target_engine, settings, profile=profile
                ).run()
            except (ValueError, SyncError, SQLAlchemyError) as error:
                logging.getLogger(__name__).error(
                    "Sync failed profile=%s source_table=%s: %s",
                    profile.name,
                    profile.source_table,
                    error,
                )
                failures.append(profile.name)
                continue
            logger.info(
                "Sync completed profile=%s source_table=%s target_table=%s rows_read=%s rows_written=%s",
                profile.name,
                report.source_table,
                report.target_table,
                report.rows_read,
                report.rows_written,
            )
    if failures:
        raise SyncError(f"Sync failed for profiles: {', '.join(failures)}")
    return 0


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
        if args.command in SYNC_COMMANDS:
            profiles = _profiles_for(args.command, settings)
            _run_profiles(settings, profiles)
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
