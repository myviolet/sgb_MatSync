"""Batched full and incremental synchronization service."""

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from sqlalchemy import Column, Engine, MetaData, String, Table, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from mysql_table_sync.config import Settings
from mysql_table_sync.exceptions import SyncError
from mysql_table_sync.mapping import build_target_row
from mysql_table_sync.sources import SyncProfile, material_move_profile

SYNC_STATE_TABLE = "_mysql_table_sync_state"
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SyncReport:
    source_table: str
    target_table: str
    rows_read: int
    rows_written: int


class TableSyncService:
    """Synchronize eligible source rows using the source primary key as a high-water cursor."""

    def __init__(
        self,
        source_engine: Engine,
        target_engine: Engine,
        settings: Settings,
        profile: SyncProfile | None = None,
    ) -> None:
        self._source_engine = source_engine
        self._target_engine = target_engine
        self._settings = settings
        self._profile = profile or material_move_profile(settings)

    def run(self) -> SyncReport:
        """Run an initial full scan, then continue from the last committed source ID."""
        profile = self._profile
        logger.info(
            "Inspecting tables profile=%s source_table=%s target_table=%s",
            profile.name,
            profile.source_table,
            self._settings.target_table,
        )
        source_metadata = MetaData()
        target_metadata = MetaData()
        try:
            source_table = Table(
                profile.source_table,
                source_metadata,
                autoload_with=self._source_engine,
            )
            target_table = Table(
                self._settings.target_table,
                target_metadata,
                autoload_with=self._target_engine,
            )
        except SQLAlchemyError as error:
            raise SyncError(f"Could not inspect source or target table: {error}") from error

        date_column = next(
            (name for name in profile.date_columns if name in source_table.c), None
        )
        source_id_name = profile.source_primary_key
        required_source_columns = {source_id_name, *profile.required_columns}
        if date_column is not None:
            required_source_columns.add(date_column)
        missing_source_columns = required_source_columns - set(source_table.c.keys())
        if missing_source_columns:
            raise SyncError(f"Source table is missing columns: {sorted(missing_source_columns)}")
        if date_column is None:
            raise SyncError(
                f"Source table is missing a posting date column, "
                f"tried: {list(profile.date_columns)}"
            )

        target_key_names = ("meter_number", "frozen_time", "stat_type")
        required_target_columns = {
            *target_key_names,
            "dvalue",
            "price",
            "update_at",
        }
        missing_target_columns = required_target_columns - set(target_table.c.keys())
        if missing_target_columns:
            raise SyncError(f"Target table is missing columns: {sorted(missing_target_columns)}")

        source_id = source_table.c[source_id_name]
        self._validate_source_ids(
            self._source_engine, source_id, profile.excluded_ids
        )

        state_metadata = MetaData()
        state_table = Table(
            SYNC_STATE_TABLE,
            state_metadata,
            Column("sync_name", String(200), primary_key=True),
            Column("last_id", String(255), nullable=True),
        )
        state_metadata.create_all(self._target_engine, checkfirst=True)
        sync_name = f"{profile.source_table}->{self._settings.target_table}"
        with self._target_engine.begin() as connection:
            state_exists = connection.execute(
                select(state_table.c.sync_name).where(state_table.c.sync_name == sync_name)
            ).first()
            if state_exists is None:
                connection.execute(insert(state_table).values(sync_name=sync_name, last_id=None))

        self._check_cursor_regression(
            source_engine=self._source_engine,
            target_engine=self._target_engine,
            state_table=state_table,
            sync_name=sync_name,
            source_id=source_id,
            profile=profile,
        )

        rows_read = 0
        rows_written = 0
        while True:
            with self._target_engine.connect() as connection:
                last_id = connection.execute(
                    select(state_table.c.last_id).where(state_table.c.sync_name == sync_name)
                ).scalar_one()

            cursor_value: Any = last_id
            if profile.numeric_primary_key and last_id is not None:
                cursor_value = int(last_id)
            source_query = select(source_table).order_by(source_id.asc()).limit(
                self._settings.batch_size
            )
            if cursor_value is not None:
                source_query = source_query.where(source_id > cursor_value)
            if profile.excluded_ids:
                source_query = source_query.where(
                    source_id.notin_(profile.excluded_ids)
                )
            with self._source_engine.connect() as connection:
                source_rows = [dict(row) for row in connection.execute(source_query).mappings()]
            if not source_rows:
                break

            batch_last_id = str(source_rows[-1][source_id_name])
            result = profile.aggregate(source_rows, date_column=date_column)
            aggregates = result.aggregates
            written_at = datetime.now()
            with self._target_engine.begin() as connection:
                locked_last_id = connection.execute(
                    select(state_table.c.last_id)
                    .where(state_table.c.sync_name == sync_name)
                    .with_for_update()
                ).scalar_one()
                if locked_last_id != last_id:
                    raise SyncError("Sync cursor changed concurrently; rerun after other sync ends")

                for aggregate in aggregates:
                    key_conditions = (
                        target_table.c.meter_number == aggregate.meter_number,
                        target_table.c.frozen_time == datetime.combine(
                            aggregate.frozen_time, datetime.min.time()
                        ),
                        target_table.c.stat_type == 1,
                    )
                    matches = connection.execute(
                        select(target_table.c.id)
                        .where(*key_conditions)
                        .limit(2)
                    ).scalars().all()
                    if len(matches) > 1:
                        raise SyncError(
                            "Target contains duplicate daily aggregate rows for "
                            f"{aggregate.meter_number} on {aggregate.frozen_time}"
                        )

                    if matches:
                        connection.execute(
                            update(target_table)
                            .where(target_table.c.id == matches[0])
                            .values(
                                dvalue=target_table.c.dvalue + aggregate.dvalue,
                                price=target_table.c.price + aggregate.price,
                                update_at=written_at,
                            )
                        )
                    else:
                        connection.execute(
                            insert(target_table).values(
                                **build_target_row(aggregate, written_at=written_at)
                            )
                        )

                connection.execute(
                    update(state_table)
                    .where(state_table.c.sync_name == sync_name)
                    .values(last_id=batch_last_id)
                )

            logger.info(
                "Committed sync batch profile=%s rows_read=%s aggregates_written=%s last_id=%s",
                profile.name,
                len(source_rows),
                len(aggregates),
                batch_last_id,
            )
            if result.skipped:
                logger.info(
                    "Skipped dirty source rows profile=%s %s",
                    profile.name,
                    " ".join(f"{reason}={count}" for reason, count in sorted(result.skipped.items())),
                )

            rows_read += len(source_rows)
            rows_written += len(aggregates)

        report = SyncReport(
            source_table=profile.source_table,
            target_table=self._settings.target_table,
            rows_read=rows_read,
            rows_written=rows_written,
        )
        logger.info(
            "Sync service finished profile=%s rows_read=%s aggregates_written=%s",
            profile.name,
            rows_read,
            rows_written,
        )
        return report

    @staticmethod
    @staticmethod
    def _check_cursor_regression(
        source_engine: Engine,
        target_engine: Engine,
        state_table: Table,
        sync_name: str,
        source_id,
        profile: SyncProfile,
    ) -> None:
        """Fail loudly when a numeric cursor points beyond the current source MAX(id).

        A truncated-and-reloaded source table would otherwise look like
        "no new rows" and silently stop syncing forever.
        """
        if not profile.numeric_primary_key:
            return
        with target_engine.connect() as connection:
            last_id = connection.execute(
                select(state_table.c.last_id).where(state_table.c.sync_name == sync_name)
            ).scalar_one()
        if last_id is None:
            return
        with source_engine.connect() as connection:
            max_id = connection.execute(select(func.max(source_id))).scalar()
        if max_id is None or int(max_id) < int(last_id):
            raise SyncError(
                f"Source primary key appears to have been reset: cursor {last_id} exceeds "
                f"MAX(id) {max_id if max_id is not None else 'NULL'} for {profile.source_table}. "
                f"If the source table was reloaded, reset the cursor with: "
                f"UPDATE {SYNC_STATE_TABLE} SET last_id = NULL WHERE sync_name = '{sync_name}'; "
                f"then rerun a full sync."
            )

    @staticmethod
    def _validate_source_ids(
        source_engine: Engine, source_id, excluded_ids: tuple[str, ...]
    ) -> None:
        with source_engine.connect() as connection:
            null_id = connection.execute(
                select(source_id).where(source_id.is_(None)).limit(1)
            ).first()
            if null_id is not None:
                raise SyncError("Source ID contains NULL; incremental cursor cannot process it")
            duplicate_query = select(source_id).group_by(source_id).having(func.count() > 1)
            if excluded_ids:
                duplicate_query = duplicate_query.where(source_id.notin_(excluded_ids))
            duplicate_id = connection.execute(duplicate_query.limit(1)).first()
            if duplicate_id is not None:
                raise SyncError(f"Source ID is not unique: {duplicate_id[0]!r}")
