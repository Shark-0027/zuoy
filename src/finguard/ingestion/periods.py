from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from finguard.models import PeriodInfo


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _make_info(raw: str, start: date | None, end: date | None, kind: str, label: str) -> PeriodInfo:
    days = None
    if start and end and kind != "instant":
        days = (end - start).days + 1
    return PeriodInfo(raw=raw, start=start, end=end, kind=kind, label=label, days=days)


def parse_period(raw: str) -> PeriodInfo:
    text = str(raw or "").strip()
    normalized = re.sub(r"\s+", "", text)
    if not normalized:
        return PeriodInfo(raw=text, label=text, kind="unknown")

    # ISO / slash date: balance-sheet instant
    match = re.fullmatch(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?:日)?", normalized)
    if match:
        year, month, day = map(int, match.groups())
        try:
            instant = date(year, month, day)
        except ValueError:
            return PeriodInfo(raw=text, label=text, kind="unknown")
        return _make_info(text, instant, instant, "instant", f"{instant.isoformat()}")

    # Full year
    match = re.fullmatch(r"(20\d{2})年?", normalized)
    if match:
        year = int(match.group(1))
        return _make_info(text, date(year, 1, 1), date(year, 12, 31), "ytd", f"{year} 全年")

    # 2025-9M, 2025 9M, 2025M9, 2025年前三季度, 2025年1-9月
    match = re.fullmatch(r"(20\d{2})[-_/]?(\d{1,2})[Mm]", normalized)
    if not match:
        match = re.fullmatch(r"(20\d{2})[Mm](\d{1,2})", normalized)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            end = date(year, month, _last_day(year, month))
            label = (
                f"{year} 前三季度"
                if month == 9
                else f"{year} 上半年"
                if month == 6
                else f"{year} 前{month}个月"
            )
            return _make_info(text, date(year, 1, 1), end, "ytd", label)

    match = re.fullmatch(r"(20\d{2})(?:年)?(?:前)?(?:三季度|1[-—至]9月|1-9月|9个月)", normalized)
    if match:
        year = int(match.group(1))
        return _make_info(text, date(year, 1, 1), date(year, 9, 30), "ytd", f"{year} 前三季度")

    match = re.fullmatch(r"(20\d{2})(?:年)?(?:前)?(?:二季度|上半年|1[-—至]6月|6个月)", normalized)
    if match:
        year = int(match.group(1))
        return _make_info(text, date(year, 1, 1), date(year, 6, 30), "ytd", f"{year} 上半年")

    # Quarter: 2025Q3 / 2025-Q3 / 2025年第3季度
    match = re.fullmatch(r"(20\d{2})[-_]?[Qq]([1-4])", normalized)
    if not match:
        match = re.fullmatch(r"(20\d{2})年第?([1-4])季度", normalized)
    if match:
        year, quarter = int(match.group(1)), int(match.group(2))
        start_month = 3 * (quarter - 1) + 1
        end_month = start_month + 2
        start = date(year, start_month, 1)
        end = date(year, end_month, _last_day(year, end_month))
        return _make_info(text, start, end, "quarter", f"{year} Q{quarter}")

    # Month: 2025-07 / 2025年7月
    match = re.fullmatch(r"(20\d{2})[-_/.](\d{1,2})", normalized)
    if not match:
        match = re.fullmatch(r"(20\d{2})年?(\d{1,2})月", normalized)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            start = date(year, month, 1)
            end = date(year, month, _last_day(year, month))
            return _make_info(text, start, end, "month", f"{year}年{month}月")

    return PeriodInfo(raw=text, label=text, kind="unknown")


def same_period_prior_year(current: PeriodInfo, candidates: list[PeriodInfo]) -> PeriodInfo | None:
    if current.kind == "unknown" or not current.end:
        return None
    for candidate in candidates:
        if candidate.kind != current.kind or not candidate.start or not candidate.end:
            continue
        if candidate.end.year != current.end.year - 1:
            continue
        if current.kind == "instant":
            if candidate.end.month == current.end.month and candidate.end.day == current.end.day:
                return candidate
        elif current.kind == "month":
            if candidate.end.month == current.end.month:
                return candidate
        elif candidate.start.month == current.start.month and candidate.end.month == current.end.month:
            return candidate
    return None


def months_in_quarter(year: int, quarter: int) -> list[PeriodInfo]:
    start_month = 3 * (quarter - 1) + 1
    result: list[PeriodInfo] = []
    for offset in range(3):
        month = start_month + offset
        first = date(year, month, 1)
        last = date(year, month, _last_day(year, month))
        result.append(_make_info(f"{year}-{month:02d}", first, last, "month", f"{year}年{month}月"))
    return result


def period_sort_key(period: PeriodInfo) -> tuple[date, date]:
    return (period.start or date.min, period.end or date.min)


def subtract_year(period: PeriodInfo) -> PeriodInfo | None:
    if not period.start or not period.end:
        return None
    try:
        start = period.start.replace(year=period.start.year - 1)
        end = period.end.replace(year=period.end.year - 1)
    except ValueError:
        return None
    kind = period.kind
    return _make_info(period.raw, start, end, kind, f"{start.year} 同期")


def period_label(period: PeriodInfo) -> str:
    return period.label or period.raw


def days_between(start: date, end: date) -> int:
    return (end - start).days + 1


def shifted_month_end(year: int, month: int) -> date:
    return date(year, month, _last_day(year, month))


def previous_day(value: date) -> date:
    return value - timedelta(days=1)
