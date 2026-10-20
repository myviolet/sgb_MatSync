"""Business rules for selecting and aggregating source consumption rows."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

MATERIAL_TYPE_CODES = {
    "硅铁": "SFE",
    "柴油": "DIE",
    "焦炭": "COK",
    "煤炭": "COA",
    "白云石": "DOL",
    "废钢": "SCR",
    "兰炭": "SEM",
    "石灰石": "LIM",
}
MATERIAL_MOVEMENT_CATEGORIES = {
    "煤炭": {
        "101": ("purchase",),
        "102": ("purchase", "moisture"),
        "291": ("usage",),
        "292": ("usage",),
    },
    "兰炭": {
        "101": ("purchase",),
        "102": ("purchase", "moisture"),
        "291": ("usage",),
        "292": ("usage",),
    },
    "焦炭": {
        "101": ("purchase",),
        "102": ("purchase", "moisture"),
        "291": ("usage",),
        "292": ("usage",),
    },
    "硅铁": {
        "105": ("purchase",),
        "201": ("usage",),
        "202": ("usage",),
        "291": ("usage",),
        "292": ("usage",),
    },
    "石灰石": {
        "101": ("purchase",),
        "102": ("purchase", "moisture"),
        "291": ("usage",),
        "292": ("usage",),
    },
    "白云石": {
        "101": ("purchase",),
        "102": ("purchase", "moisture"),
        "291": ("usage",),
        "292": ("usage",),
    },
    "柴油": {
        "101": ("purchase",),
        "201": ("usage",),
    },
    "废钢": {
        "101": ("purchase",),
        "102": ("purchase",),
        "105": ("purchase",),
        "201": ("usage",),
        "202": ("usage",),
        "291": ("usage",),
        "292": ("usage",),
    },
}
CATEGORY_SUFFIXES = {"usage": "", "purchase": "_P", "moisture": "_MC"}
TARGET_QUANTITY_UNIT = "t"
KG_TO_TONNES = Decimal("0.001")
DIESEL_DENSITY_KG_PER_LITRE = Decimal("0.831")
MATERIAL_SOURCE_UNITS = {
    "硅铁": frozenset({"kg"}),
    "柴油": frozenset({"l"}),
    "焦炭": frozenset({"kg"}),
    "煤炭": frozenset({"kg"}),
    "白云石": frozenset({"kg"}),
    "废钢": frozenset({"kg"}),
    "兰炭": frozenset({"kg", "t", "to"}),
    "石灰石": frozenset({"kg"}),
}


@dataclass(frozen=True, slots=True)
class ConsumptionAggregate:
    """One daily aggregate ready for target-field mapping."""

    material_type: str
    meter_number: str
    frozen_time: date
    dvalue: Decimal
    price: Decimal
    source_rows: int


@dataclass(slots=True)
class _DailyTotals:
    quantity: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    source_rows: int = 0


def _parse_decimal(value: Any, field_name: str, source_id: Any) -> Decimal:
    text_value = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", text_value):
        raise ValueError(f"Invalid {field_name} in source ID {source_id!r}: {text_value!r}")
    try:
        number = Decimal(text_value.replace(",", ""))
    except InvalidOperation as error:
        raise ValueError(f"Invalid {field_name} in source ID {source_id!r}") from error
    if not number.is_finite():
        raise ValueError(f"{field_name} must be finite in source ID {source_id!r}")
    return number


def _to_base_quantity(material_type: str, unit: str, quantity: Decimal) -> Decimal:
    allowed_units = MATERIAL_SOURCE_UNITS[material_type]
    if unit not in allowed_units:
        raise ValueError(
            f"Unsupported Unit {unit!r} for MaterialType {material_type!r}; "
            f"expected one of {sorted(allowed_units)!r}"
        )
    if unit == "kg":
        return quantity * KG_TO_TONNES
    if material_type == "柴油" and unit == "l":
        return quantity * DIESEL_DENSITY_KG_PER_LITRE * KG_TO_TONNES
    return quantity


def map_consumption_rows(
    source_row: Mapping[str, Any], excluded_ids: tuple[str, ...] = ("OnlyKey",)
) -> list[dict[str, Any]]:
    """Map one source row to its usage, purchase, and/or moisture target categories."""
    movement_type = str(source_row.get("MovementType") or "").strip()
    source_id = str(source_row.get("ID") or "").strip()
    material_type = str(source_row.get("MaterialType") or "").strip()
    material = str(source_row.get("Material") or "").strip()

    if source_id in excluded_ids:
        return []

    type_code = MATERIAL_TYPE_CODES.get(material_type)
    if type_code is None or not material:
        return []

    categories = MATERIAL_MOVEMENT_CATEGORIES.get(material_type, {}).get(movement_type, ())
    if not categories:
        return []

    base_meter_number = f"{type_code}{material}"
    return [
        {**source_row, "meter_number": f"{base_meter_number}{CATEGORY_SUFFIXES[category]}"}
        for category in categories
    ]


def map_consumption_row(
    source_row: Mapping[str, Any], excluded_ids: tuple[str, ...] = ("OnlyKey",)
) -> dict[str, Any] | None:
    """Return the first mapped target category for compatibility with single-row callers."""
    mapped_rows = map_consumption_rows(source_row, excluded_ids)
    return mapped_rows[0] if mapped_rows else None


def aggregate_consumption_rows(
    source_rows: Iterable[Mapping[str, Any]],
    excluded_ids: tuple[str, ...] = ("OnlyKey",),
) -> list[ConsumptionAggregate]:
    """Filter rows and combine quantity/CNY by mapped material number and day."""
    grouped: dict[tuple[str, date], tuple[str, _DailyTotals]] = {}

    for source_row in source_rows:
        mapped_rows = map_consumption_rows(source_row, excluded_ids)
        if not mapped_rows:
            continue

        material_type = str(source_row["MaterialType"]).strip()
        try:
            posting_date = datetime.strptime(
                str(source_row.get("PostingDate") or "").strip(), "%Y%m%d"
            ).date()
        except ValueError as error:
            raise ValueError(
                f"Invalid PostingDate in source ID {source_row.get('ID')!r}"
            ) from error

        quantity = _parse_decimal(source_row.get("Qty"), "Qty", source_row.get("ID"))
        amount = _parse_decimal(source_row.get("CNY"), "CNY", source_row.get("ID"))
        unit = str(source_row.get("Unit") or "").strip().casefold()
        base_quantity = _to_base_quantity(material_type, unit, quantity)
        for mapped_row in mapped_rows:
            detail_meter_number = mapped_row["meter_number"]
            category_suffix = detail_meter_number[len(MATERIAL_TYPE_CODES[material_type] + str(source_row["Material"]).strip()):]
            summary_meter_number = f"{MATERIAL_TYPE_CODES[material_type]}_ALL{category_suffix}"
            for meter_number in (detail_meter_number, summary_meter_number):
                group_key = (meter_number, posting_date)
                material_and_totals = grouped.get(group_key)
                if material_and_totals is None:
                    aggregate = _DailyTotals()
                    grouped[group_key] = (material_type, aggregate)
                else:
                    _, aggregate = material_and_totals
                aggregate.quantity += base_quantity
                aggregate.price += amount
                aggregate.source_rows += 1

    return [
        ConsumptionAggregate(
            material_type=material_type,
            meter_number=meter_number,
            frozen_time=posting_date,
            dvalue=values.quantity,
            price=values.price,
            source_rows=values.source_rows,
        )
        for (meter_number, posting_date), (material_type, values) in grouped.items()
    ]


def build_target_row(
    aggregate: ConsumptionAggregate, written_at: datetime | None = None
) -> dict[str, Any]:
    """Build target insert values, omitting nullable period fields intentionally."""
    return {
        "frozen_time": datetime.combine(aggregate.frozen_time, datetime.min.time()),
        "meter_number": aggregate.meter_number,
        "dvalue": aggregate.dvalue,
        "price": aggregate.price,
        "update_at": written_at or datetime.now(),
        "stat_type": 1,
    }
