"""Per-source-table synchronization profiles and their aggregation rules."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from mysql_table_sync.config import Settings
from mysql_table_sync.mapping import (
    ConsumptionAggregate,
    aggregate_consumption_rows_with_skip_counts,
)

# 生铁销量表没有 MaterialType 列，其类型短码独立于 MATERIAL_TYPE_CODES 定义。
# 不并入 MATERIAL_TYPE_CODES，避免让物料移动表误接受 "生铁" 这一 MaterialType。
PIG_IRON_TYPE_CODE = "pig"
PIG_IRON_MATERIAL_TAIL_LENGTH = 6
# 所有生铁品种的每日销售总量，口径与物料移动表的 <短码>_all 一致。
PIG_IRON_ALL_METER_NUMBER = f"{PIG_IRON_TYPE_CODE}_all"
PIG_IRON_SALES_TABLE = "SAP_PG_Sales"
PRODUCTIVITY_TABLE = "SAP_Productivity"

# SAP_Productivity 的 Product 维度 -> meter_number 短码，整体小写。
# Iron=炼铁, Pipe=管道, Fittings=管件。
PRODUCTIVITY_PRODUCT_CODES = {"iron": "iron", "pipe": "pipe", "fittings": "fit"}
# 所有成品品种的每日总产量，口径与生铁销量的 pig_all 一致。
PRODUCTIVITY_ALL_METER_NUMBER = "prod_all"

_SALES_DATE_PATTERN = re.compile(r"\d{4}\.\d{2}\.\d{2}")
_SALES_DATE_FORMAT = "%Y.%m.%d"
_PRODUCTIVITY_DATE_PATTERN = re.compile(r"\d{8}")
_PRODUCTIVITY_DATE_FORMAT = "%Y%m%d"
_DECIMAL_PATTERN = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?")
_BOM = "\ufeff"


@dataclass(frozen=True, slots=True)
class DailyAggregate:
    """One daily aggregate for the single-value sources (sales / productivity)."""

    meter_number: str
    frozen_time: date
    dvalue: Decimal
    price: Decimal
    source_rows: int


@dataclass(frozen=True, slots=True)
class AggregationResult:
    aggregates: list[Any]
    skipped: dict[str, int]


@dataclass(frozen=True, slots=True)
class SyncProfile:
    """Everything the generic sync engine needs to know about one source table."""

    name: str
    source_table: str
    source_primary_key: str
    required_columns: frozenset[str]
    date_columns: tuple[str, ...]
    aggregate: Callable[..., AggregationResult]
    numeric_primary_key: bool = False
    excluded_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class _DailyTotals:
    dvalue: Decimal = Decimal("0")
    source_rows: int = 0


def _parse_source_date(value: Any, pattern: re.Pattern[str], date_format: str) -> date | None:
    """Return the parsed date, or None when the value is dirty data to be skipped."""
    text = str(value if value is not None else "").replace(_BOM, "").strip()
    if pattern.fullmatch(text) is None:
        return None
    try:
        return datetime.strptime(text, date_format).date()
    except ValueError:
        return None


def _parse_ton(value: Any) -> Decimal | None:
    """Return the parsed tonnage, or None when the value is dirty data to be skipped."""
    text = str(value if value is not None else "").replace(_BOM, "").strip()
    if _DECIMAL_PATTERN.fullmatch(text) is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _build_daily_aggregates(
    grouped: dict[tuple[str, date], _DailyTotals],
) -> list[DailyAggregate]:
    return [
        DailyAggregate(
            meter_number=meter_number,
            frozen_time=posting_date,
            dvalue=totals.dvalue,
            price=Decimal("0"),
            source_rows=totals.source_rows,
        )
        for (meter_number, posting_date), totals in grouped.items()
    ]


def _roll_up_daily_totals(
    grouped: dict[tuple[str, date], _DailyTotals], meter_number: str
) -> dict[tuple[str, date], _DailyTotals]:
    """Sum every meter number of one day into a single ``<root>_all`` row.

    Derived from the per-material (or per-product) totals rather than from the
    raw rows, so the all-row and its details always agree exactly.
    """
    all_totals: dict[tuple[str, date], _DailyTotals] = {}
    for (_meter_number, posting_date), totals in grouped.items():
        day_totals = all_totals.setdefault((meter_number, posting_date), _DailyTotals())
        day_totals.dvalue += totals.dvalue
        day_totals.source_rows += totals.source_rows
    return all_totals


def aggregate_pig_iron_sales_rows(
    source_rows: Iterable[Mapping[str, Any]], *, date_column: str
) -> AggregationResult:
    """Group pig iron sales by Material (last 6 digits) and day, summing Ton.

    An extra ``pig_all`` row per day holds the total across every material.
    Sales and productivity sources carry no amount column, so the target price
    is always zero. Both sources are append-only, so quantities are accumulated
    with their source sign untouched.
    """
    grouped: dict[tuple[str, date], _DailyTotals] = {}
    skipped: Counter[str] = Counter()

    for source_row in source_rows:
        posting_date = _parse_source_date(
            source_row.get(date_column), _SALES_DATE_PATTERN, _SALES_DATE_FORMAT
        )
        if posting_date is None:
            skipped["invalid_posting_date"] += 1
            continue
        material = str(source_row.get("Material") or "").strip()
        if len(material) < PIG_IRON_MATERIAL_TAIL_LENGTH:
            skipped["invalid_material"] += 1
            continue
        ton = _parse_ton(source_row.get("Ton"))
        if ton is None:
            skipped["invalid_ton"] += 1
            continue
        meter_number = f"{PIG_IRON_TYPE_CODE}{material[-PIG_IRON_MATERIAL_TAIL_LENGTH:]}".lower()
        totals = grouped.setdefault((meter_number, posting_date), _DailyTotals())
        totals.dvalue += ton
        totals.source_rows += 1

    aggregates = _build_daily_aggregates(grouped)
    aggregates.extend(
        _build_daily_aggregates(
            _roll_up_daily_totals(grouped, PIG_IRON_ALL_METER_NUMBER)
        )
    )
    return AggregationResult(aggregates, dict(skipped))


def aggregate_productivity_rows(
    source_rows: Iterable[Mapping[str, Any]], *, date_column: str
) -> AggregationResult:
    """Group finished-product output by Product and day, summing Ton.

    An extra ``prod_all`` row per day holds the total across every product.
    """
    grouped: dict[tuple[str, date], _DailyTotals] = {}
    skipped: Counter[str] = Counter()

    for source_row in source_rows:
        posting_date = _parse_source_date(
            source_row.get(date_column), _PRODUCTIVITY_DATE_PATTERN, _PRODUCTIVITY_DATE_FORMAT
        )
        if posting_date is None:
            skipped["invalid_posting_date"] += 1
            continue
        product = str(source_row.get("Product") or "").strip().casefold()
        product_code = PRODUCTIVITY_PRODUCT_CODES.get(product)
        if product_code is None:
            skipped["unknown_product"] += 1
            continue
        ton = _parse_ton(source_row.get("Ton"))
        if ton is None:
            skipped["invalid_ton"] += 1
            continue
        totals = grouped.setdefault((product_code, posting_date), _DailyTotals())
        totals.dvalue += ton
        totals.source_rows += 1

    aggregates = _build_daily_aggregates(grouped)
    aggregates.extend(
        _build_daily_aggregates(
            _roll_up_daily_totals(grouped, PRODUCTIVITY_ALL_METER_NUMBER)
        )
    )
    return AggregationResult(aggregates, dict(skipped))


def _make_consumption_aggregator(
    excluded_ids: tuple[str, ...],
) -> Callable[..., AggregationResult]:
    def aggregate(
        source_rows: Iterable[Mapping[str, Any]], *, date_column: str
    ) -> AggregationResult:
        aggregates, skipped = aggregate_consumption_rows_with_skip_counts(
            source_rows, excluded_ids=excluded_ids, date_column=date_column
        )
        return AggregationResult(aggregates=aggregates, skipped=skipped)

    return aggregate


def material_move_profile(settings: Settings) -> SyncProfile:
    """Build the material-move profile, keeping the env-driven settings backward compatible."""
    return SyncProfile(
        name="material_move",
        source_table=settings.source_table,
        source_primary_key=settings.source_primary_key,
        required_columns=frozenset(
            {"MovementType", "MaterialType", "Material", "Qty", "Unit", "CNY"}
        ),
        date_columns=("PostingDate",),
        aggregate=_make_consumption_aggregator(settings.source_excluded_ids),
        excluded_ids=settings.source_excluded_ids,
        # SOURCE_PRIMARY_KEY=sync_id switches to the customer-side auto-increment column:
        # numeric cursor comparisons plus the cursor-regression guard. The legacy varchar
        # ID keeps the old string-cursor behaviour, so this stays an env-only rollback.
        numeric_primary_key=settings.source_primary_key.casefold() != "id",
    )


PIG_IRON_SALES_PROFILE = SyncProfile(
    name="pig_iron_sales",
    source_table=PIG_IRON_SALES_TABLE,
    source_primary_key="id",
    required_columns=frozenset({"Material", "Ton"}),
    date_columns=("Posting_Date",),
    aggregate=aggregate_pig_iron_sales_rows,
    numeric_primary_key=True,
)

PRODUCTIVITY_PROFILE = SyncProfile(
    name="productivity",
    source_table=PRODUCTIVITY_TABLE,
    source_primary_key="id",
    required_columns=frozenset({"Product", "Ton"}),
    date_columns=("Posting_Date",),
    aggregate=aggregate_productivity_rows,
    numeric_primary_key=True,
)
