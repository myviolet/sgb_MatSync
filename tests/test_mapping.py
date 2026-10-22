import pytest
from decimal import Decimal
from datetime import datetime

from mysql_table_sync.mapping import (
    MATERIAL_SOURCE_UNITS,
    MATERIAL_TYPE_CODES,
    aggregate_consumption_rows,
    build_target_row,
    map_consumption_row,
    map_consumption_rows,
)


@pytest.mark.parametrize("movement_type", ["201", "202", "291", "292"])
def test_consumption_movement_type_is_selected(movement_type: str) -> None:
    source_row = {
        "ID": "5003792664+2026+0012",
        "MovementType": movement_type,
        "MaterialType": "硅铁",
        "Material": "20056684",
        "Qty": "1.25",
    }

    mapped_row = map_consumption_row(source_row)

    assert mapped_row is not None
    assert mapped_row["meter_number"] == "sfe20056684"
    assert mapped_row["Qty"] == "1.25"


def test_moisture_movement_type_uses_separate_meter_number_suffix() -> None:
    mapped_rows = map_consumption_rows(
        {
            "ID": "moisture-row",
            "MovementType": "102",
            "MaterialType": "煤炭",
            "Material": "20056684",
        }
    )

    assert {row["meter_number"] for row in mapped_rows} == {
        "coa20056684_p",
        "coa20056684_mc",
    }


@pytest.mark.parametrize(
    ("material_type", "movement_type", "expected_suffixes"),
    [
        ("煤炭", "101", ("_p",)), ("煤炭", "102", ("_p", "_mc")),
        ("煤炭", "291", ("",)), ("煤炭", "292", ("",)),
        ("兰炭", "101", ("_p",)), ("兰炭", "102", ("_p", "_mc")),
        ("兰炭", "291", ("",)), ("兰炭", "292", ("",)),
        ("焦炭", "101", ("_p",)), ("焦炭", "102", ("_p", "_mc")),
        ("焦炭", "291", ("",)), ("焦炭", "292", ("",)),
        ("硅铁", "105", ("_p",)), ("硅铁", "201", ("",)),
        ("硅铁", "202", ("",)), ("硅铁", "291", ("",)), ("硅铁", "292", ("",)),
        ("石灰石", "101", ("_p",)), ("石灰石", "102", ("_p", "_mc")),
        ("石灰石", "291", ("",)), ("石灰石", "292", ("",)),
        ("白云石", "101", ("_p",)), ("白云石", "102", ("_p", "_mc")),
        ("白云石", "291", ("",)), ("白云石", "292", ("",)),
        ("柴油", "101", ("_p",)), ("柴油", "201", ("",)),
        ("废钢", "101", ("_p",)), ("废钢", "102", ("_p",)),
        ("废钢", "105", ("_p",)), ("废钢", "201", ("",)),
        ("废钢", "202", ("",)), ("废钢", "291", ("",)), ("废钢", "292", ("",)),
    ],
)
def test_material_type_movement_type_matrix(
    material_type: str, movement_type: str, expected_suffixes: tuple[str, ...]
) -> None:
    code = MATERIAL_TYPE_CODES[material_type]
    mapped_rows = map_consumption_rows(
        {
            "ID": "purchase-row",
            "MovementType": movement_type,
            "MaterialType": material_type,
            "Material": "20056684",
        }
    )

    assert {row["meter_number"] for row in mapped_rows} == {
        f"{code}20056684{suffix}" for suffix in expected_suffixes
    }


def test_every_material_type_maps_to_expected_code() -> None:
    for material_type, expected_code in MATERIAL_TYPE_CODES.items():
        mapped_row = map_consumption_row(
            {
                "ID": "material-row",
                "MovementType": "105" if material_type == "硅铁" else "101",
                "MaterialType": material_type,
                "Material": "MAT001",
            }
        )
        assert mapped_row is not None
        assert mapped_row["meter_number"] == f"{expected_code}mat001_p"


@pytest.mark.parametrize(
    "source_row",
    [
        {"ID": "row-1", "MovementType": "106", "MaterialType": "硅铁", "Material": "M1"},
        {"ID": "row-1", "MovementType": "201", "MaterialType": "未知物料", "Material": "M1"},
        {"ID": "OnlyKey", "MovementType": "201", "MaterialType": "硅铁", "Material": "M1"},
    ],
)
def test_non_consumption_unknown_material_and_excluded_id_are_skipped(source_row) -> None:
    assert map_consumption_row(source_row) is None


def test_excluded_ids_can_be_overridden() -> None:
    source_row = {
        "ID": "custom-bad-id",
        "MovementType": "201",
        "MaterialType": "硅铁",
        "Material": "M1",
    }

    assert map_consumption_row(source_row, excluded_ids=("OnlyKey",)) is not None


def test_daily_rows_are_aggregated_by_material_and_posting_date() -> None:
    rows = [
        {
            "ID": "row-1",
            "MovementType": "291",
            "MaterialType": "兰炭",
            "Material": "MAT-A",
            "PostingDate": "20261009",
            "Qty": "1000",
            "Unit": "kg",
            "CNY": "12.30",
        },
        {
            "ID": "row-2",
            "MovementType": "292",
            "MaterialType": "兰炭",
            "Material": "MAT-A",
            "PostingDate": "20261009",
            "Qty": "2",
            "Unit": "t",
            "CNY": "3.40",
        },
        {
            "ID": "row-3",
            "MovementType": "291",
            "MaterialType": "兰炭",
            "Material": "MAT-A",
            "PostingDate": "20261010",
            "Qty": "1",
            "Unit": "t",
            "CNY": "4.00",
        },
    ]

    aggregates = aggregate_consumption_rows(rows)

    aggregates_by_key = {(item.meter_number, item.frozen_time): item for item in aggregates}
    first_day = aggregates_by_key[("semmat-a", datetime.strptime("20261009", "%Y%m%d").date())]
    all_day = aggregates_by_key[("sem_all", datetime.strptime("20261009", "%Y%m%d").date())]
    assert len(aggregates) == 4
    assert first_day.dvalue == Decimal("3")
    assert first_day.price == Decimal("15.70")
    assert first_day.source_rows == 2
    assert all_day.dvalue == Decimal("3")
    assert all_day.price == Decimal("15.70")
    assert all_day.source_rows == 2


def test_daily_aggregation_separates_different_material_numbers() -> None:
    rows = [
        {
            "ID": "row-1",
            "MovementType": "291",
            "MaterialType": "硅铁",
            "Material": "MAT-A",
            "PostingDate": "20261009",
            "Qty": "1000",
            "Unit": "kg",
            "CNY": "10",
        },
        {
            "ID": "row-2",
            "MovementType": "291",
            "MaterialType": "硅铁",
            "Material": "MAT-B",
            "PostingDate": "20261009",
            "Qty": "2000",
            "Unit": "kg",
            "CNY": "20",
        },
    ]

    aggregates = aggregate_consumption_rows(rows)

    assert {item.meter_number for item in aggregates} == {"sfemat-a", "sfemat-b", "sfe_all"}
    assert {item.dvalue for item in aggregates} == {
        Decimal("1.000"), Decimal("2.000"), Decimal("3.000")
    }
    all_materials = next(item for item in aggregates if item.meter_number == "sfe_all")
    assert all_materials.dvalue == Decimal("3.000")
    assert all_materials.price == Decimal("30")


def test_moisture_and_usage_rows_are_aggregated_separately() -> None:
    rows = [
        {
            "ID": "usage-row",
            "MovementType": "291",
            "MaterialType": "煤炭",
            "Material": "MAT-A",
            "PostingDate": "20261009",
            "Qty": "1000",
            "Unit": "kg",
            "CNY": "10",
        },
        {
            "ID": "moisture-row",
            "MovementType": "102",
            "MaterialType": "煤炭",
            "Material": "MAT-A",
            "PostingDate": "20261009",
            "Qty": "500",
            "Unit": "kg",
            "CNY": "0",
        },
    ]

    aggregates = aggregate_consumption_rows(rows)

    assert len(aggregates) == 6
    aggregates_by_meter = {item.meter_number: item for item in aggregates}
    assert aggregates_by_meter["coamat-a"].dvalue == Decimal("1.000")
    assert aggregates_by_meter["coamat-a_p"].dvalue == Decimal("0.500")
    assert aggregates_by_meter["coamat-a_mc"].dvalue == Decimal("0.500")
    assert aggregates_by_meter["coa_all"].dvalue == Decimal("1.000")
    assert aggregates_by_meter["coa_all_p"].dvalue == Decimal("0.500")
    assert aggregates_by_meter["coa_all_mc"].dvalue == Decimal("0.500")


def test_movement_102_is_aggregated_as_both_purchase_and_moisture() -> None:
    row = {
        "ID": "purchase-moisture-row",
        "MovementType": "102",
        "MaterialType": "煤炭",
        "Material": "MAT-A",
        "PostingDate": "20261009",
        "Qty": "1000",
        "Unit": "kg",
        "CNY": "10",
    }

    aggregates = aggregate_consumption_rows([row])

    assert {item.meter_number for item in aggregates} == {
        "coamat-a_p", "coamat-a_mc", "coa_all_p", "coa_all_mc"
    }
    assert all(item.dvalue == Decimal("1.000") for item in aggregates)
    assert all(item.price == Decimal("10") for item in aggregates)


def test_daily_all_group_combines_purchase_rows_across_material_numbers() -> None:
    rows = [
        {
            "ID": "purchase-a",
            "MovementType": "101",
            "MaterialType": "煤炭",
            "Material": "MAT-A",
            "PostingDate": "20261009",
            "Qty": "1000",
            "Unit": "kg",
            "CNY": "10",
        },
        {
            "ID": "purchase-b",
            "MovementType": "102",
            "MaterialType": "煤炭",
            "Material": "MAT-B",
            "PostingDate": "20261009",
            "Qty": "2000",
            "Unit": "kg",
            "CNY": "20",
        },
    ]

    aggregates = aggregate_consumption_rows(rows)
    by_meter = {item.meter_number: item for item in aggregates}

    assert by_meter["coa_all_p"].dvalue == Decimal("3.000")
    assert by_meter["coa_all_p"].price == Decimal("30")
    assert by_meter["coa_all_p"].source_rows == 2
    assert by_meter["coamat-a_p"].dvalue == Decimal("1.000")
    assert by_meter["coamat-b_p"].dvalue == Decimal("2.000")


@pytest.mark.parametrize(
    ("material_type", "unit", "expected_quantity"),
    [
        ("硅铁", "kg", Decimal("0.0025")),
        ("柴油", "l", Decimal("0.0020775")),
        ("焦炭", "KG", Decimal("2.5")),
        ("兰炭", "t", Decimal("2.5")),
        ("兰炭", "TO", Decimal("2.5")),
        ("兰炭", "kg", Decimal("0.0025")),
    ],
)
def test_material_units_are_normalized(
    material_type: str, unit: str, expected_quantity: Decimal
) -> None:
    movement_type = "201" if material_type in {"硅铁", "柴油", "废钢"} else "291"
    aggregate = aggregate_consumption_rows(
        [
            {
                "ID": "row-1",
                "MovementType": movement_type,
                "MaterialType": material_type,
                "Material": "MAT001",
                "PostingDate": "20261009",
                "Qty": "2.5",
                "Unit": unit,
                "CNY": "1",
            }
        ]
    )[0]

    assert aggregate.dvalue == expected_quantity
    assert aggregate.price == Decimal("1")
    assert MATERIAL_SOURCE_UNITS[material_type]


def test_coke_quantity_is_kept_in_kilograms_while_others_stay_in_tonnes() -> None:
    def aggregate_for(material_type: str) -> object:
        return aggregate_consumption_rows(
            [
                {
                    "ID": "row-1",
                    "MovementType": "291",
                    "MaterialType": material_type,
                    "Material": "MAT001",
                    "PostingDate": "20261009",
                    "Qty": "2500",
                    "Unit": "kg",
                    "CNY": "100",
                }
            ]
        )[0]

    assert aggregate_for("焦炭").dvalue == Decimal("2500")
    assert aggregate_for("煤炭").dvalue == Decimal("2.5")


def test_usage_quantity_is_stored_as_a_positive_value() -> None:
    aggregates = aggregate_consumption_rows(
        [
            {
                "ID": "usage-row",
                "MovementType": "201",
                "MaterialType": "硅铁",
                "Material": "MAT001",
                "PostingDate": "20261009",
                "Qty": "-2.5",
                "Unit": "kg",
                "CNY": "12.30",
            }
        ]
    )

    by_meter = {item.meter_number: item for item in aggregates}

    assert by_meter["sfemat001"].dvalue == Decimal("0.0025")
    assert by_meter["sfe_all"].dvalue == Decimal("0.0025")


def test_purchase_keeps_sign_while_moisture_turns_positive() -> None:
    aggregates = aggregate_consumption_rows(
        [
            {
                "ID": "purchase-moisture-row",
                "MovementType": "102",
                "MaterialType": "煤炭",
                "Material": "MAT001",
                "PostingDate": "20261009",
                "Qty": "-1000",
                "Unit": "kg",
                "CNY": "10",
            }
        ]
    )

    by_meter = {item.meter_number: item for item in aggregates}

    assert by_meter["coamat001_p"].dvalue == Decimal("-1.000")
    assert by_meter["coamat001_mc"].dvalue == Decimal("1.000")
    assert by_meter["coa_all_p"].dvalue == Decimal("-1.000")
    assert by_meter["coa_all_mc"].dvalue == Decimal("1.000")


def test_unsupported_unit_fails_instead_of_misaggregating() -> None:
    with pytest.raises(ValueError, match="Unsupported Unit"):
        aggregate_consumption_rows(
            [
                {
                    "ID": "row-1",
                    "MovementType": "201",
                    "MaterialType": "硅铁",
                    "Material": "MAT001",
                    "Material": "MAT001",
                    "PostingDate": "20261009",
                    "Qty": "2.5",
                    "Unit": "t",
                    "CNY": "1",
                }
            ]
        )


def test_invalid_posting_date_fails_clearly() -> None:
    with pytest.raises(ValueError, match="Invalid PostingDate"):
        aggregate_consumption_rows(
            [
                {
                    "ID": "row-1",
                    "MovementType": "201",
                    "MaterialType": "硅铁",
                    "Material": "MAT001",
                    "Material": "MAT001",
                    "PostingDate": "2026-10-09",
                    "Qty": "2.5",
                    "Unit": "kg",
                    "CNY": "1",
                }
            ]
        )


def test_grouped_thousands_separators_are_parsed_as_decimal_numbers() -> None:
    aggregate = aggregate_consumption_rows(
        [
            {
                "ID": "row-comma",
                "MovementType": "201",
                "MaterialType": "硅铁",
                "Material": "MAT001",
                "Material": "MAT001",
                "PostingDate": "20261009",
                "Qty": "-2,000",
                "Unit": "KG",
                "CNY": "-10,709.36",
            }
        ]
    )[0]

    # MovementType 201 is a usage movement, so the negative quantity is stored as positive.
    assert aggregate.dvalue == Decimal("2.000")
    assert aggregate.price == Decimal("-10709.36")


@pytest.mark.parametrize("invalid_number", ["1,00", "1,000.2,3", "one"])
def test_malformed_numeric_grouping_fails_clearly(invalid_number: str) -> None:
    with pytest.raises(ValueError, match="Invalid Qty"):
        aggregate_consumption_rows(
            [
                {
                    "ID": "bad-number",
                    "MovementType": "201",
                    "MaterialType": "硅铁",
                    "Material": "MAT001",
                    "PostingDate": "20261009",
                    "Qty": invalid_number,
                    "Unit": "KG",
                    "CNY": "1.00",
                }
            ]
        )


def test_target_row_sets_write_time_and_stat_type_and_omits_period_fields() -> None:
    aggregate = aggregate_consumption_rows(
        [
            {
                "ID": "row-1",
                "MovementType": "201",
                "MaterialType": "硅铁",
                "Material": "MAT001",
                "PostingDate": "20261009",
                "Qty": "2.5",
                "Unit": "kg",
                "CNY": "12.30",
            }
        ]
    )[0]
    written_at = datetime(2026, 10, 9, 12, 34, 56)

    target_row = build_target_row(aggregate, written_at=written_at)

    assert target_row == {
        "frozen_time": datetime(2026, 10, 9),
        "meter_number": "sfemat001",
        "dvalue": Decimal("0.0025"),
        "price": Decimal("12.30"),
        "update_at": written_at,
        "stat_type": 1,
    }
    assert not {"last_value", "current_value", "last_time", "current_time"} & target_row.keys()
