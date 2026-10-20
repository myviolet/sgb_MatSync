import pytest
from sqlalchemy import create_engine, text

from mysql_table_sync.config import Settings
from mysql_table_sync.exceptions import SyncError
from mysql_table_sync.service import TableSyncService


def _create_test_tables(source_engine, target_engine) -> None:
    with source_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE SAP_RW_Material_Move ("
                "ID VARCHAR(50), MovementType VARCHAR(10), MaterialType VARCHAR(50), Material VARCHAR(50), "
                "PostingDate VARCHAR(8), Qty VARCHAR(50), Unit VARCHAR(10), CNY VARCHAR(50))"
            )
        )
    with target_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE record_frozen_data ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, frozen_time DATETIME NOT NULL, "
                "meter_number VARCHAR(50) NOT NULL, dvalue NUMERIC(20, 4) NOT NULL, "
                "price NUMERIC(15, 2) NOT NULL, update_at DATETIME, stat_type INTEGER NOT NULL, "
                "last_value NUMERIC, current_value NUMERIC, last_time DATETIME, current_time DATETIME)"
            )
        )


def _insert_source_row(
    source_engine,
    row_id: str,
    quantity: str,
    amount: str,
    movement_type: str = "201",
    material_type: str = "硅铁",
) -> None:
    with source_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO SAP_RW_Material_Move "
                "(ID, MovementType, MaterialType, Material, PostingDate, Qty, Unit, CNY) "
                "VALUES (:id, :movement_type, :material_type, '20056684', '20261009', :quantity, 'kg', :amount)"
            ),
            {
                "id": row_id,
                "movement_type": movement_type,
                "material_type": material_type,
                "quantity": quantity,
                "amount": amount,
            },
        )


def test_incremental_cursor_does_not_stall_on_excluded_id() -> None:
    """Excluded IDs sort after every numeric source ID, so they must never become the cursor."""
    source_engine = create_engine("sqlite://")
    target_engine = create_engine("sqlite://")
    settings = Settings(
        "sqlite://",
        "sqlite://",
        batch_size=2,
        source_excluded_ids=("OnlyKey",),
    )
    _create_test_tables(source_engine, target_engine)
    _insert_source_row(source_engine, "0001+2026+0001", "1", "10.00", "201")
    _insert_source_row(source_engine, "OnlyKey", "999", "999.00", "201")

    try:
        service = TableSyncService(source_engine, target_engine, settings)
        first_run = service.run()

        assert first_run.rows_read == 1
        assert first_run.rows_written == 2

        _insert_source_row(source_engine, "0001+2026+0002", "2", "20.00", "201")
        second_run = service.run()

        assert second_run.rows_read == 1
        assert second_run.rows_written == 2

        with target_engine.connect() as connection:
            last_id = connection.execute(
                text("SELECT last_id FROM _mysql_table_sync_state")
            ).scalar_one()

        assert last_id == "0001+2026+0002"
    finally:
        source_engine.dispose()
        target_engine.dispose()


def test_duplicate_excluded_ids_do_not_block_sync() -> None:
    """Junk IDs repeat in the source table; they are never synced, so they must not fail validation."""
    source_engine = create_engine("sqlite://")
    target_engine = create_engine("sqlite://")
    settings = Settings(
        "sqlite://",
        "sqlite://",
        batch_size=10,
        source_excluded_ids=("OnlyKey",),
    )
    _create_test_tables(source_engine, target_engine)
    _insert_source_row(source_engine, "0001+2026+0001", "1", "10.00", "201")
    _insert_source_row(source_engine, "OnlyKey", "999", "999.00", "201")
    _insert_source_row(source_engine, "OnlyKey", "888", "888.00", "201")

    try:
        service = TableSyncService(source_engine, target_engine, settings)
        report = service.run()

        assert report.rows_read == 1
        assert report.rows_written == 2

        with target_engine.connect() as connection:
            last_id = connection.execute(
                text("SELECT last_id FROM _mysql_table_sync_state")
            ).scalar_one()

        assert last_id == "0001+2026+0001"
    finally:
        source_engine.dispose()
        target_engine.dispose()


def test_duplicate_synced_id_still_fails() -> None:
    """The uniqueness guard must stay active for rows that actually get synced."""
    source_engine = create_engine("sqlite://")
    target_engine = create_engine("sqlite://")
    settings = Settings(
        "sqlite://",
        "sqlite://",
        batch_size=10,
        source_excluded_ids=("OnlyKey",),
    )
    _create_test_tables(source_engine, target_engine)
    _insert_source_row(source_engine, "0001+2026+0001", "1", "10.00", "201")
    _insert_source_row(source_engine, "0001+2026+0001", "2", "20.00", "201")

    try:
        service = TableSyncService(source_engine, target_engine, settings)

        with pytest.raises(SyncError, match="not unique"):
            service.run()
    finally:
        source_engine.dispose()
        target_engine.dispose()


def test_full_then_incremental_sync_uses_source_id_cursor() -> None:
    source_engine = create_engine("sqlite://")
    target_engine = create_engine("sqlite://")
    settings = Settings(
        "sqlite://",
        "sqlite://",
        batch_size=2,
        source_excluded_ids=("OnlyKey",),
    )
    _create_test_tables(source_engine, target_engine)
    _insert_source_row(source_engine, "0001+2026+0001", "1", "10.00", "201")
    _insert_source_row(source_engine, "0001+2026+0002", "2", "20.00", "201")
    _insert_source_row(source_engine, "0001+2026+0003", "3", "30.00", "201")

    try:
        service = TableSyncService(source_engine, target_engine, settings)
        first_run = service.run()
        second_run = service.run()

        assert first_run.rows_read == 3
        assert first_run.rows_written == 4
        assert second_run.rows_read == 0
        assert second_run.rows_written == 0

        _insert_source_row(source_engine, "0001+2026+0004", "4", "40.00", "201")
        incremental_run = service.run()

        assert incremental_run.rows_read == 1
        assert incremental_run.rows_written == 2
        with target_engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT frozen_time, meter_number, dvalue, price, stat_type, "
                    'last_value, current_value, last_time, "current_time" '
                    "FROM record_frozen_data"
                )
            ).mappings().all()

        assert len(rows) == 2
        rows_by_meter = {row["meter_number"]: row for row in rows}
        for meter_number in ("SFE20056684", "SFE_ALL"):
            assert float(rows_by_meter[meter_number]["dvalue"]) == 0.01
            assert float(rows_by_meter[meter_number]["price"]) == 100.0
            assert rows_by_meter[meter_number]["stat_type"] == 1
            assert rows_by_meter[meter_number]["last_value"] is None
            assert rows_by_meter[meter_number]["current_value"] is None
            assert rows_by_meter[meter_number]["last_time"] is None
            assert rows_by_meter[meter_number]["current_time"] is None
    finally:
        source_engine.dispose()
        target_engine.dispose()


def test_sync_writes_moisture_and_usage_to_separate_meter_rows() -> None:
    source_engine = create_engine("sqlite://")
    target_engine = create_engine("sqlite://")
    settings = Settings("sqlite://", "sqlite://", source_excluded_ids=("OnlyKey",))
    _create_test_tables(source_engine, target_engine)
    _insert_source_row(source_engine, "0001+2026+0001", "1000", "10.00", "291", "煤炭")
    _insert_source_row(source_engine, "0001+2026+0002", "500", "0.00", "102", "煤炭")

    try:
        TableSyncService(source_engine, target_engine, settings).run()
        with target_engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT meter_number, dvalue FROM record_frozen_data "
                    "ORDER BY meter_number"
                )
            ).all()

        assert rows == [
            ("COA20056684", 1),
            ("COA20056684_MC", 0.5),
            ("COA20056684_P", 0.5),
            ("COA_ALL", 1),
            ("COA_ALL_MC", 0.5),
            ("COA_ALL_P", 0.5),
        ]
    finally:
        source_engine.dispose()
        target_engine.dispose()
