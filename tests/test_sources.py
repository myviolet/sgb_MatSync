from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from mysql_table_sync.config import Settings
from mysql_table_sync.exceptions import SyncError
from mysql_table_sync.sources import (
    PIG_IRON_SALES_PROFILE,
    PRODUCTIVITY_PROFILE,
    aggregate_pig_iron_sales_rows,
    aggregate_productivity_rows,
    material_move_profile,
)
from mysql_table_sync.cli import _profiles_for
from mysql_table_sync.service import TableSyncService


def _by_meter(result):
    return {(item.meter_number, item.frozen_time): item for item in result.aggregates}


def test_sales_rows_are_grouped_by_material_tail_and_day() -> None:
    result = aggregate_pig_iron_sales_rows(
        [
            {
                "id": 1,
                "Posting_Date": "2026.07.03",
                "Material": "000000000000226460",
                "Ton": "57.82",
            },
            {
                "id": 2,
                "Posting_Date": "2026.07.03",
                "Material": "000000000000226460",
                "Ton": "42.00",
            },
            {
                "id": 3,
                "Posting_Date": "2026.07.04",
                "Material": "000000000000226460",
                "Ton": "59.77",
            },
            {
                "id": 4,
                "Posting_Date": "2026.07.03",
                "Material": "000000000000226461",
                "Ton": "1,234.50",
            },
        ],
        date_column="Posting_Date",
    )

    by_meter = _by_meter(result)
    assert set(by_meter) == {
        ("pig226460", date(2026, 7, 3)),
        ("pig226460", date(2026, 7, 4)),
        ("pig226461", date(2026, 7, 3)),
    }
    assert by_meter[("pig226460", date(2026, 7, 3))].dvalue == Decimal("99.82")
    assert by_meter[("pig226460", date(2026, 7, 3))].price == Decimal("0")
    assert by_meter[("pig226460", date(2026, 7, 3))].source_rows == 2
    assert by_meter[("pig226460", date(2026, 7, 4))].dvalue == Decimal("59.77")
    assert by_meter[("pig226461", date(2026, 7, 3))].dvalue == Decimal("1234.50")


def test_sales_dirty_header_and_placeholder_dates_are_skipped() -> None:
    result = aggregate_pig_iron_sales_rows(
        [
            {
                "id": 1,
                "Posting_Date": "\ufeffPosting date",  # Excel header row with BOM
                "Material": "000000000000226460",
                "Ton": "57.82",
            },
            {"id": 2, "Posting_Date": "Posting_Date", "Material": "226460", "Ton": "1.00"},
            {"id": 3, "Posting_Date": "-", "Material": "226460", "Ton": "1.00"},
            {"id": 4, "Posting_Date": None, "Material": "226460", "Ton": "1.00"},
            {"id": 5, "Posting_Date": "", "Material": "226460", "Ton": "1.00"},
            {"id": 6, "Posting_Date": "2026-07-03", "Material": "226460", "Ton": "1.00"},
            {"id": 7, "Posting_Date": "20260703", "Material": "226460", "Ton": "1.00"},
            {"id": 8, "Posting_Date": "2026.07.03", "Material": "226460", "Ton": "1.00"},
        ],
        date_column="Posting_Date",
    )

    assert result.skipped == {"invalid_posting_date": 7}
    assert _by_meter(result)[("pig226460", date(2026, 7, 3))].dvalue == Decimal("1.00")


def test_sales_invalid_ton_and_short_material_are_skipped() -> None:
    result = aggregate_pig_iron_sales_rows(
        [
            {"id": 1, "Posting_Date": "2026.07.03", "Material": "226460", "Ton": "abc"},
            {"id": 2, "Posting_Date": "2026.07.03", "Material": "226460", "Ton": None},
            {"id": 3, "Posting_Date": "2026.07.03", "Material": "12345", "Ton": "1.00"},
            {"id": 4, "Posting_Date": "2026.07.03", "Material": "", "Ton": "1.00"},
            {"id": 5, "Posting_Date": "2026.07.03", "Material": "226460", "Ton": "-5.00"},
        ],
        date_column="Posting_Date",
    )

    assert result.skipped == {"invalid_material": 2, "invalid_ton": 2}
    assert _by_meter(result)[("pig226460", date(2026, 7, 3))].dvalue == Decimal("-5.00")


def test_sales_keeps_the_source_sign_instead_of_absorbing_it() -> None:
    result = aggregate_pig_iron_sales_rows(
        [
            {"id": 1, "Posting_Date": "2026.07.03", "Material": "226460", "Ton": "-57.82"},
        ],
        date_column="Posting_Date",
    )

    assert _by_meter(result)[("pig226460", date(2026, 7, 3))].dvalue == Decimal("-57.82")


def test_productivity_rows_are_grouped_by_product_and_day() -> None:
    result = aggregate_productivity_rows(
        [
            {"id": 1, "Posting_Date": "20260710", "Product": "Iron", "Ton": "100.5"},
            {"id": 2, "Posting_Date": "20260710", "Product": "Iron", "Ton": "50"},
            {"id": 3, "Posting_Date": "20260711", "Product": "Pipe", "Ton": "20"},
            {"id": 4, "Posting_Date": "20260710", "Product": "Fittings", "Ton": "5.25"},
            {"id": 5, "Posting_Date": "20260710", "Product": " fittings ", "Ton": "1.00"},
        ],
        date_column="Posting_Date",
    )

    by_meter = _by_meter(result)
    assert set(by_meter) == {
        ("iron", date(2026, 7, 10)),
        ("pipe", date(2026, 7, 11)),
        ("fit", date(2026, 7, 10)),
    }
    assert by_meter[("iron", date(2026, 7, 10))].dvalue == Decimal("150.5")
    assert by_meter[("iron", date(2026, 7, 10))].price == Decimal("0")
    assert by_meter[("pipe", date(2026, 7, 11))].dvalue == Decimal("20")
    assert by_meter[("fit", date(2026, 7, 10))].dvalue == Decimal("6.25")


def test_productivity_dirty_rows_are_skipped() -> None:
    result = aggregate_productivity_rows(
        [
            {"id": 1, "Posting_Date": "\ufeffPosting date", "Product": "Iron", "Ton": "1"},
            {"id": 2, "Posting_Date": "2026-07-10", "Product": "Iron", "Ton": "1"},
            {"id": 3, "Posting_Date": "20260710", "Product": "未知产品", "Ton": "1"},
            {"id": 4, "Posting_Date": "20260710", "Product": None, "Ton": "1"},
            {"id": 5, "Posting_Date": "20260710", "Product": "Iron", "Ton": "-"},
            {"id": 6, "Posting_Date": "20260710", "Product": "Iron", "Ton": "1.5"},
        ],
        date_column="Posting_Date",
    )

    assert result.skipped == {
        "invalid_posting_date": 2,
        "unknown_product": 2,
        "invalid_ton": 1,
    }
    assert _by_meter(result)[("iron", date(2026, 7, 10))].dvalue == Decimal("1.5")


def _create_new_source_tables(source_engine) -> None:
    with source_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE SAP_PG_Sales ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, Posting_Date VARCHAR(50), "
                "OrderNumber VARCHAR(50), Material VARCHAR(50), MaterialDes VARCHAR(50), "
                "Product VARCHAR(50), Category VARCHAR(50), SalesType VARCHAR(50), "
                "Ton VARCHAR(50), Plant VARCHAR(50))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE SAP_Productivity ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, Product VARCHAR(50), "
                "Posting_Date VARCHAR(50), Ton VARCHAR(50))"
            )
        )


def _create_target_table(target_engine) -> None:
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


def _insert_sales_row(source_engine, posting_date, material, ton) -> int:
    with source_engine.begin() as connection:
        return connection.execute(
            text(
                "INSERT INTO SAP_PG_Sales (Posting_Date, Material, Ton) "
                "VALUES (:d, :m, :t)"
            ),
            {"d": posting_date, "m": material, "t": ton},
        ).lastrowid


def _insert_productivity_row(source_engine, posting_date, product, ton) -> int:
    with source_engine.begin() as connection:
        return connection.execute(
            text(
                "INSERT INTO SAP_Productivity (Posting_Date, Product, Ton) "
                "VALUES (:d, :p, :t)"
            ),
            {"d": posting_date, "p": product, "t": ton},
        ).lastrowid


def test_sales_sync_end_to_end_with_numeric_cursor() -> None:
    source_engine = create_engine("sqlite://")
    target_engine = create_engine("sqlite://")
    settings = Settings("sqlite://", "sqlite://", batch_size=2)
    _create_new_source_tables(source_engine)
    _create_target_table(target_engine)
    _insert_sales_row(source_engine, "\ufeffPosting date", "226460", "999.99")
    _insert_sales_row(source_engine, "2026.07.03", "000000000000226460", "57.82")
    _insert_sales_row(source_engine, "2026.07.03", "000000000000226460", "42.00")
    _insert_sales_row(source_engine, "2026.07.04", "000000000000226460", "59.77")
    _insert_sales_row(source_engine, "-", "226460", "1.00")
    _insert_sales_row(source_engine, "2026.07.04", "000000000000226461", "10")

    try:
        service = TableSyncService(
            source_engine, target_engine, settings, profile=PIG_IRON_SALES_PROFILE
        )
        first_run = service.run()

        assert first_run.rows_read == 6
        # rows_written counts per-batch writes, so a key split across batches counts twice.
        assert first_run.rows_written == 4
        second_run = service.run()
        assert second_run.rows_read == 0

        _insert_sales_row(source_engine, "2026.07.04", "000000000000226460", "1.23")
        incremental_run = service.run()
        assert incremental_run.rows_read == 1
        assert incremental_run.rows_written == 1

        with target_engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT meter_number, frozen_time, dvalue, price, stat_type "
                    "FROM record_frozen_data ORDER BY meter_number, frozen_time"
                )
            ).all()
            last_id = connection.execute(
                text(
                    "SELECT last_id FROM _mysql_table_sync_state "
                    "WHERE sync_name = 'SAP_PG_Sales->record_frozen_data'"
                )
            ).scalar_one()

        rows_by_key = {
            (meter, str(day)[:10]): (Decimal(str(dvalue)), Decimal(str(price)), stat_type)
            for meter, day, dvalue, price, stat_type in rows
        }
        assert rows_by_key[("pig226460", "2026-07-03")] == (Decimal("99.82"), Decimal("0"), 1)
        assert rows_by_key[("pig226460", "2026-07-04")] == (Decimal("61.00"), Decimal("0"), 1)
        assert rows_by_key[("pig226461", "2026-07-04")] == (Decimal("10"), Decimal("0"), 1)
        assert len(rows) == 3
        assert last_id == "7"
    finally:
        source_engine.dispose()
        target_engine.dispose()


def test_numeric_cursor_survives_string_ordering_trap() -> None:
    """ids 9 and 10 must follow numeric order, not lexicographic ('9' > '10')."""
    source_engine = create_engine("sqlite://")
    target_engine = create_engine("sqlite://")
    settings = Settings("sqlite://", "sqlite://", batch_size=1)
    _create_new_source_tables(source_engine)
    _create_target_table(target_engine)
    for index in range(1, 13):
        _insert_productivity_row(source_engine, "20260710", "Iron", str(index))

    try:
        service = TableSyncService(
            source_engine, target_engine, settings, profile=PRODUCTIVITY_PROFILE
        )
        report = service.run()

        assert report.rows_read == 12
        with target_engine.connect() as connection:
            total = connection.execute(
                text("SELECT dvalue FROM record_frozen_data WHERE meter_number = 'iron'")
            ).scalar_one()
            last_id = connection.execute(
                text(
                    "SELECT last_id FROM _mysql_table_sync_state "
                    "WHERE sync_name = 'SAP_Productivity->record_frozen_data'"
                )
            ).scalar_one()

        assert total == Decimal("78")
        assert last_id == "12"
    finally:
        source_engine.dispose()
        target_engine.dispose()


def test_profiles_keep_independent_state_rows() -> None:
    source_engine = create_engine("sqlite://")
    target_engine = create_engine("sqlite://")
    settings = Settings("sqlite://", "sqlite://")
    _create_new_source_tables(source_engine)
    _create_target_table(target_engine)
    _insert_sales_row(source_engine, "2026.07.03", "226460", "1.00")
    _insert_productivity_row(source_engine, "20260710", "Pipe", "2.00")

    try:
        TableSyncService(
            source_engine, target_engine, settings, profile=PIG_IRON_SALES_PROFILE
        ).run()
        TableSyncService(
            source_engine, target_engine, settings, profile=PRODUCTIVITY_PROFILE
        ).run()

        with target_engine.connect() as connection:
            state_rows = connection.execute(
                text("SELECT sync_name FROM _mysql_table_sync_state ORDER BY sync_name")
            ).scalars().all()
            rows = connection.execute(
                text("SELECT meter_number, dvalue FROM record_frozen_data ORDER BY meter_number")
            ).all()

        assert state_rows == ["SAP_PG_Sales->record_frozen_data", "SAP_Productivity->record_frozen_data"]
        assert rows == [("pig226460", Decimal("1.00")), ("pipe", Decimal("2.00"))]
    finally:
        source_engine.dispose()
        target_engine.dispose()


def test_numeric_cursor_reset_is_detected() -> None:
    """A truncated-and-reloaded source must fail loudly instead of silently skipping."""
    source_engine = create_engine("sqlite://")
    target_engine = create_engine("sqlite://")
    settings = Settings("sqlite://", "sqlite://")
    _create_new_source_tables(source_engine)
    _create_target_table(target_engine)
    _insert_productivity_row(source_engine, "20260710", "Iron", "1.00")
    _insert_productivity_row(source_engine, "20260711", "Iron", "2.00")

    try:
        service = TableSyncService(
            source_engine, target_engine, settings, profile=PRODUCTIVITY_PROFILE
        )
        service.run()

        # Simulate a reload that reset the auto-increment counter below the cursor.
        with target_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE _mysql_table_sync_state SET last_id = '999' "
                    "WHERE sync_name = 'SAP_Productivity->record_frozen_data'"
                )
            )

        with pytest.raises(SyncError, match="reset the cursor"):
            service.run()

        # A cleared source table is the same failure mode (MAX(id) is NULL).
        with source_engine.begin() as connection:
            connection.execute(text("DELETE FROM SAP_Productivity"))

        with pytest.raises(SyncError, match="reset the cursor"):
            service.run()
    finally:
        source_engine.dispose()
        target_engine.dispose()


def test_cli_expands_commands_into_profiles() -> None:
    settings = Settings("sqlite://", "sqlite://")

    assert [p.name for p in _profiles_for("sync", settings)] == ["material_move"]
    assert [p.name for p in _profiles_for("sync-sales", settings)] == ["pig_iron_sales"]
    assert [p.name for p in _profiles_for("sync-productivity", settings)] == ["productivity"]
    assert [p.name for p in _profiles_for("sync-all", settings)] == [
        "material_move",
        "pig_iron_sales",
        "productivity",
    ]
    assert material_move_profile(settings).source_table == "SAP_RW_Material_Move"
