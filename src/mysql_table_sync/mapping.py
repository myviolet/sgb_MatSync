"""Business rules for selecting and aggregating source consumption rows."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

MATERIAL_TYPE_CODES = {
    "硅铁": "sfe",
    "柴油": "die",
    "焦炭": "cok",
    "煤炭": "coa",
    "白云石": "dol",
    "废钢": "scr",
    "兰炭": "sem",
    "石灰石": "lim",
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
CATEGORY_SUFFIXES = {"usage": "", "purchase": "_p", "moisture": "_mc"}
ALL_MATERIALS_SUFFIX = "_all"
# Source reverses a posting with an equal opposite entry, so every category total is netted by sign
# first. Moisture is stored as a positive value afterwards; usage keeps the signed net.
ABSOLUTE_VALUE_CATEGORIES = frozenset({"moisture"})
# Extra usage rows keyed by a cost object field, stored alongside the plain usage row.
# A field mapped to None applies to every material type; otherwise it is restricted to those listed.
# "CO" identifies plant / process steps, "CC" identifies the diesel (柴油) cost centre.
COST_OBJECT_SOURCE_FIELDS = {"CO": None, "CC": frozenset({"柴油"})}
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
# Target dvalue unit per material. 焦炭 stays in kg, everything else is stored in tonnes.
MATERIAL_TARGET_UNITS = {
    "硅铁": "t",
    "柴油": "t",
    "焦炭": "kg",
    "煤炭": "t",
    "白云石": "t",
    "废钢": "t",
    "兰炭": "t",
    "石灰石": "t",
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
    absolute_value: bool = False


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
        kilograms = quantity
    elif material_type == "柴油" and unit == "l":
        kilograms = quantity * DIESEL_DENSITY_KG_PER_LITRE
    else:
        # 兰炭的 t / to 本身已是吨，直接作为目标值
        return quantity

    if MATERIAL_TARGET_UNITS[material_type] == "kg":
        return kilograms
    return kilograms * KG_TO_TONNES


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

    base_meter_number = f"{type_code}{material}".lower()
    mapped_rows = [
        {
            **source_row,
            "category": category,
            "meter_number": f"{base_meter_number}{CATEGORY_SUFFIXES[category]}",
            "contributes_to_summary": True,
        }
        for category in categories
    ]
    if "usage" in categories:
        for field, allowed_material_types in COST_OBJECT_SOURCE_FIELDS.items():
            cost_object = str(source_row.get(field) or "").strip()
            if not cost_object:
                continue
            if allowed_material_types is not None and material_type not in allowed_material_types:
                continue
            mapped_rows.append(
                {
                    **source_row,
                    "category": "usage",
                    "meter_number": f"{base_meter_number}{cost_object}".lower(),
                    "contributes_to_summary": False,
                }
            )
    return mapped_rows


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
            category = mapped_row["category"]
            summary_meter_number = (
                f"{MATERIAL_TYPE_CODES[material_type]}"
                f"{ALL_MATERIALS_SUFFIX}{CATEGORY_SUFFIXES[category]}"
            )
            needs_absolute_value = category in ABSOLUTE_VALUE_CATEGORIES
            meter_numbers = [mapped_row["meter_number"]]
            if mapped_row["contributes_to_summary"]:
                meter_numbers.append(summary_meter_number)
            for meter_number in meter_numbers:
                group_key = (meter_number, posting_date)
                material_and_totals = grouped.get(group_key)
                if material_and_totals is None:
                    aggregate = _DailyTotals()
                    aggregate.absolute_value = needs_absolute_value
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
            dvalue=abs(values.quantity) if values.absolute_value else values.quantity,
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
