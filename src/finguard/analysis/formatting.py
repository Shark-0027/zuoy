from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from finguard.models import Metric


def dec(value: Decimal | float | int | None) -> Decimal:
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_dec(value: Decimal | None, places: int = 2) -> Decimal | None:
    if value is None:
        return None
    quant = Decimal(1).scaleb(-places)
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def fmt_number(value: Decimal | float | int | None, places: int = 2) -> str:
    if value is None:
        return "—"
    quant = Decimal(1).scaleb(-places)
    number = dec(value).quantize(quant, rounding=ROUND_HALF_UP)
    return f"{number:,.{places}f}"


def fmt_yuan(value: Decimal | float | int | None, places: int = 2) -> str:
    if value is None:
        return "—"
    return f"¥{fmt_number(value, places)}"


def fmt_wan(value: Decimal | float | int | None, places: int = 1) -> str:
    if value is None:
        return "—"
    return f"¥{fmt_number(dec(value) / Decimal(10000), places)} 万"


def fmt_pct(value: Decimal | float | int | None, places: int = 1, signed: bool = False) -> str:
    if value is None:
        return "—"
    prefix = "+" if signed and dec(value) > 0 else ""
    return f"{prefix}{fmt_number(value, places)}%"


def fmt_pp(value: Decimal | float | int | None, places: int = 1) -> str:
    if value is None:
        return "—"
    sign = "+" if dec(value) > 0 else ""
    return f"{sign}{fmt_number(value, places)}pp"


def fmt_days(value: Decimal | float | int | None, places: int = 1) -> str:
    if value is None:
        return "—"
    return f"{fmt_number(value, places)} 天"


def fmt_ratio(value: Decimal | float | int | None, places: int = 2) -> str:
    if value is None:
        return "—"
    return fmt_number(value, places)


def metric_display(metric: Metric) -> dict[str, str]:
    if metric.unit == "CNY":
        value = fmt_wan(metric.value)
        prior = fmt_wan(metric.prior)
        if metric.change_type == "percent":
            delta = fmt_pct(metric.delta, signed=True)
        else:
            delta = fmt_wan(metric.delta)
    elif metric.unit == "%":
        value = fmt_pct(metric.value)
        prior = fmt_pct(metric.prior)
        delta = fmt_pp(metric.delta) if metric.change_type == "pp" else fmt_pct(metric.delta, signed=True)
    elif metric.unit == "天":
        value = fmt_days(metric.value)
        prior = fmt_days(metric.prior)
        delta = fmt_days(metric.delta)
    else:
        value = fmt_ratio(metric.value)
        prior = fmt_ratio(metric.prior)
        delta = f"{'+' if (metric.delta or 0) > 0 else ''}{fmt_ratio(metric.delta)}"
    return {"value": value, "prior": prior, "delta": delta}
