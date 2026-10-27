from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from finguard.ingestion.periods import months_in_quarter, parse_period
from finguard.models import Dataset, Fact, Metric, PeriodInfo


def _dec(value: Decimal | None) -> Decimal | None:
    return value if value is not None else None


def _safe_div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _pct(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    ratio = _safe_div(numerator, denominator)
    return ratio * 100 if ratio is not None else None


def _change_pct(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior) * 100


def _difference(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    if current is None or prior is None:
        return None
    return current - prior


def _ev(fact: Fact | None) -> list[str]:
    return [f"EV-{fact.id}"] if fact is not None else []


def _merge_evidence(*facts: Fact | None) -> list[str]:
    result: list[str] = []
    for fact in facts:
        for evidence_id in _ev(fact):
            if evidence_id not in result:
                result.append(evidence_id)
    return result


@dataclass
class FactIndex:
    facts: list[Fact]
    by_field_period: dict[tuple[str, str], Fact] = field(default_factory=dict)
    by_field_end: dict[tuple[str, date], Fact] = field(default_factory=dict)

    @classmethod
    def build(cls, dataset: Dataset) -> FactIndex:
        valid = [fact for fact in dataset.facts if fact.status == "valid" and fact.value is not None and fact.field]
        index = cls(facts=valid)
        for fact in valid:
            key = (fact.field or "", fact.period_raw)
            index.by_field_period.setdefault(key, fact)
            if fact.period_end:
                end_key = (fact.field or "", fact.period_end)
                index.by_field_end.setdefault(end_key, fact)
        return index

    def get(self, field: str, period_raw: str) -> Fact | None:
        return self.by_field_period.get((field, period_raw))

    def at(self, field: str, end: date | None) -> Fact | None:
        if end is None:
            return None
        return self.by_field_end.get((field, end))

    def monthly(self, field: str, year: int, month: int) -> Fact | None:
        for (candidate_field, _), fact in self.by_field_period.items():
            if candidate_field != field or fact.period_kind != "month" or not fact.period_end:
                continue
            if fact.period_end.year == year and fact.period_end.month == month:
                return fact
        return None


@dataclass
class AnalysisContext:
    dataset: Dataset
    current_period: PeriodInfo | None
    prior_period: PeriodInfo | None
    current_balance_date: date | None
    prior_balance_date: date | None
    days: int | None
    values: dict[str, Decimal | None] = field(default_factory=dict)
    metrics: list[Metric] = field(default_factory=list)
    facts: dict[str, Fact | None] = field(default_factory=dict)
    monthly: dict[str, Any] = field(default_factory=dict)

    @property
    def period_label(self) -> str:
        return self.current_period.label if self.current_period else "期间未识别"

    @property
    def prior_period_label(self) -> str:
        return self.prior_period.label if self.prior_period else "上年同期未识别"

    def metric(self, metric_id: str) -> Metric | None:
        return next((metric for metric in self.metrics if metric.id == metric_id), None)


def _pick_current_flow_period(index: FactIndex) -> tuple[PeriodInfo | None, PeriodInfo | None]:
    revenue_facts = [
        fact
        for fact in index.facts
        if fact.field == "revenue" and fact.period_end and fact.period_kind in {"ytd", "quarter"}
    ]
    if not revenue_facts:
        return None, None
    kind_priority = {"ytd": 0, "quarter": 1}
    revenue_facts.sort(key=lambda fact: (fact.period_end, kind_priority.get(fact.period_kind, 9)))
    current_fact = revenue_facts[-1]
    current = parse_period(current_fact.period_raw)
    prior_candidates = [
        parse_period(fact.period_raw)
        for fact in index.facts
        if fact.field == "revenue"
        and fact.period_kind == current.kind
        and fact.period_end is not None
        and current.end is not None
        and fact.period_end.year == current.end.year - 1
        and (
            (current.kind == "ytd" and fact.period_end.month == current.end.month and fact.period_start and current.start and fact.period_start.month == current.start.month)
            or (current.kind == "quarter" and fact.period_end.month == current.end.month)
        )
    ]
    prior = max(prior_candidates, key=lambda period: period.end or date.min) if prior_candidates else None
    return current, prior


def _monthly_quarter_context(
    index: FactIndex,
    current: PeriodInfo | None,
    prior: PeriodInfo | None,
) -> dict[str, Any]:
    if not current or not current.end:
        return {}
    end = current.end
    quarter = (end.month - 1) // 3 + 1
    months = months_in_quarter(end.year, quarter)
    current_month_facts = [index.monthly("revenue", m.end.year, m.end.month) for m in months if m.end]
    prior_month_facts = []
    if prior and prior.end:
        prior_quarter = (prior.end.month - 1) // 3 + 1
        prior_months = months_in_quarter(prior.end.year, prior_quarter)
        prior_month_facts = [index.monthly("revenue", m.end.year, m.end.month) for m in prior_months if m.end]
    current_complete = all(fact is not None for fact in current_month_facts)
    prior_complete = all(fact is not None for fact in prior_month_facts)
    last_fact = current_month_facts[-1] if current_month_facts and current_month_facts[-1] else None
    prior_last_fact = prior_month_facts[-1] if prior_month_facts and prior_month_facts[-1] else None
    current_q = sum((fact.value or Decimal(0) for fact in current_month_facts if fact), Decimal(0)) if current_complete else None
    prior_q = sum((fact.value or Decimal(0) for fact in prior_month_facts if fact), Decimal(0)) if prior_complete else None
    return {
        "quarter": quarter,
        "months": months,
        "current_month_facts": current_month_facts,
        "prior_month_facts": prior_month_facts,
        "current_last_month": last_fact,
        "prior_last_month": prior_last_fact,
        "current_quarter_revenue": current_q,
        "prior_quarter_revenue": prior_q,
        "current_share_pct": _pct(last_fact.value if last_fact else None, current_q),
        "prior_share_pct": _pct(prior_last_fact.value if prior_last_fact else None, prior_q),
        "last_month_label": f"{end.year}年{end.month}月",
    }


def _metric(
    metric_id: str,
    label: str,
    value: Decimal | None,
    prior: Decimal | None,
    *,
    unit: str,
    formula: str,
    period: str,
    prior_period: str,
    evidence_ids: list[str],
    status: str = "computed",
    status_label: str = "可计算",
    limitation: str = "",
    note: str = "",
    change_type: str = "absolute",
    delta: Decimal | None = None,
    extra: dict[str, Any] | None = None,
) -> Metric:
    if status == "computed":
        if change_type == "percent":
            computed_delta = _change_pct(value, prior)
        elif change_type == "pp":
            computed_delta = _difference(value, prior)
        else:
            computed_delta = _difference(value, prior)
        if delta is not None:
            computed_delta = delta
    else:
        computed_delta = None
    if status == "computed" and value is None:
        status = "not_computable"
        status_label = "不可计算"
    return Metric(
        id=metric_id,
        label=label,
        value=value,
        prior=prior,
        delta=computed_delta,
        unit=unit,
        change_type=change_type,
        formula=formula,
        status=status,  # type: ignore[arg-type]
        status_label=status_label,
        period=period,
        prior_period=prior_period,
        evidence_ids=evidence_ids,
        limitation=limitation,
        note=note,
        extra=extra or {},
    )


def _working_capital_context(index: FactIndex, current_balance: date | None, prior_balance: date | None) -> dict[str, Any]:
    fields = {
        "accounts_receivable": "应收账款",
        "other_receivables": "其他应收款",
        "inventory": "存货",
        "accounts_payable": "应付账款",
    }
    current = {canonical_field: index.at(canonical_field, current_balance) for canonical_field in fields}
    prior = {canonical_field: index.at(canonical_field, prior_balance) for canonical_field in fields}
    changes: dict[str, Decimal | None] = {}
    for canonical_field in fields:
        if (
            current[canonical_field]
            and prior[canonical_field]
            and current[canonical_field].value is not None
            and prior[canonical_field].value is not None
        ):
            changes[canonical_field] = current[canonical_field].value - prior[canonical_field].value
        else:
            changes[canonical_field] = None
    complete = all(changes[canonical_field] is not None for canonical_field in ("accounts_receivable", "other_receivables", "inventory", "accounts_payable"))
    drag = None
    if complete:
        drag = (
            (changes["accounts_receivable"] or Decimal(0))
            + (changes["other_receivables"] or Decimal(0))
            + (changes["inventory"] or Decimal(0))
            - (changes["accounts_payable"] or Decimal(0))
        )
    return {
        "fields": fields,
        "current": current,
        "prior": prior,
        "changes": changes,
        "complete": complete,
        "drag": drag,
    }


def compute_metrics(dataset: Dataset) -> AnalysisContext:
    index = FactIndex.build(dataset)
    current_period, prior_period = _pick_current_flow_period(index)
    current_raw = current_period.raw if current_period else ""
    prior_raw = prior_period.raw if prior_period else ""
    current_end = current_period.end if current_period else None
    prior_end = prior_period.end if prior_period else None
    current_label = current_period.label if current_period else "期间未识别"
    prior_label = prior_period.label if prior_period else "上年同期未识别"
    days = current_period.days if current_period else None

    fields = (
        "revenue",
        "cogs",
        "selling_expenses",
        "admin_expenses",
        "rd_expenses",
        "finance_expenses",
        "other_income_net",
        "income_tax",
        "net_profit",
        "operating_cash_flow",
        "accounts_receivable",
        "other_receivables",
        "inventory",
        "accounts_payable",
        "total_assets",
        "total_liabilities",
        "equity",
    )
    facts: dict[str, Fact | None] = {}
    for canonical_field in fields:
        if canonical_field in {"accounts_receivable", "other_receivables", "inventory", "accounts_payable", "total_assets", "total_liabilities", "equity"}:
            facts[canonical_field] = index.at(canonical_field, current_end)
        else:
            facts[canonical_field] = index.get(canonical_field, current_raw)
    prior_facts = {}
    for canonical_field in fields:
        if canonical_field in {"accounts_receivable", "other_receivables", "inventory", "accounts_payable", "total_assets", "total_liabilities", "equity"}:
            prior_facts[canonical_field] = index.at(canonical_field, prior_end)
        else:
            prior_facts[canonical_field] = index.get(canonical_field, prior_raw)

    values: dict[str, Decimal | None] = {}
    prior_values: dict[str, Decimal | None] = {}
    for canonical_field in fields:
        values[canonical_field] = facts[canonical_field].value if facts[canonical_field] else None
        prior_values[canonical_field] = prior_facts[canonical_field].value if prior_facts[canonical_field] else None

    gross_profit = (
        values["revenue"] - values["cogs"]
        if values["revenue"] is not None and values["cogs"] is not None
        else None
    )
    prior_gross_profit = (
        prior_values["revenue"] - prior_values["cogs"]
        if prior_values["revenue"] is not None and prior_values["cogs"] is not None
        else None
    )
    gross_margin = _pct(gross_profit, values["revenue"])
    prior_gross_margin = _pct(prior_gross_profit, prior_values["revenue"])
    net_margin = _pct(values["net_profit"], values["revenue"])
    prior_net_margin = _pct(prior_values["net_profit"], prior_values["revenue"])
    selling_ratio = _pct(values["selling_expenses"], values["revenue"])
    prior_selling_ratio = _pct(prior_values["selling_expenses"], prior_values["revenue"])
    ocf_ratio = _safe_div(values["operating_cash_flow"], values["net_profit"])
    prior_ocf_ratio = _safe_div(prior_values["operating_cash_flow"], prior_values["net_profit"])
    ar_growth = _change_pct(values["accounts_receivable"], prior_values["accounts_receivable"])
    or_growth = _change_pct(values["other_receivables"], prior_values["other_receivables"])
    or_asset_ratio = _pct(values["other_receivables"], values["total_assets"])
    prior_or_asset_ratio = _pct(prior_values["other_receivables"], prior_values["total_assets"])
    ar_turnover = _safe_div(values["accounts_receivable"], values["revenue"])
    ar_turnover_days = ar_turnover * Decimal(days) if ar_turnover is not None and days else None
    prior_ar_turnover_days = (
        _safe_div(prior_values["accounts_receivable"], prior_values["revenue"]) * Decimal(days)
        if _safe_div(prior_values["accounts_receivable"], prior_values["revenue"]) is not None and days
        else None
    )
    working_capital = _working_capital_context(index, current_end, prior_end)
    monthly = _monthly_quarter_context(index, current_period, prior_period)

    values.update(
        {
            "gross_profit": gross_profit,
            "gross_margin": gross_margin,
            "net_margin": net_margin,
            "selling_expense_ratio": selling_ratio,
            "ocf_to_net_profit": ocf_ratio,
            "ar_growth": ar_growth,
            "other_receivables_growth": or_growth,
            "other_receivables_ratio": or_asset_ratio,
            "ar_turnover_days": ar_turnover_days,
            "working_capital_drag": working_capital["drag"],
            "september_revenue_share": monthly.get("current_share_pct"),
        }
    )
    prior_values.update(
        {
            "gross_profit": prior_gross_profit,
            "gross_margin": prior_gross_margin,
            "net_margin": prior_net_margin,
            "selling_expense_ratio": prior_selling_ratio,
            "ocf_to_net_profit": prior_ocf_ratio,
            "ar_turnover_days": prior_ar_turnover_days,
            "other_receivables_ratio": prior_or_asset_ratio,
            "september_revenue_share": monthly.get("prior_share_pct"),
        }
    )

    metrics: list[Metric] = []

    def add(
        metric_id: str,
        label: str,
        value_key: str,
        prior_key: str | None,
        *,
        unit: str = "CNY",
        change_type: str = "percent",
        formula: str = "",
        evidence_ids: list[str] | None = None,
        note: str = "",
        limitation: str = "",
        status: str = "computed",
        status_label: str = "可计算",
        delta: Decimal | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Metric:
        metric = _metric(
            metric_id,
            label,
            values.get(value_key),
            prior_values.get(prior_key) if prior_key else None,
            unit=unit,
            change_type=change_type,
            formula=formula,
            period=current_label,
            prior_period=prior_label,
            evidence_ids=evidence_ids or [],
            status=status,
            status_label=status_label,
            limitation=limitation,
            note=note,
            delta=delta,
            extra=extra,
        )
        metrics.append(metric)
        return metric

    add("revenue", "营业收入", "revenue", "revenue", formula="本期利润表营业收入", evidence_ids=_merge_evidence(facts["revenue"], prior_facts["revenue"]))
    add("cogs", "营业成本", "cogs", "cogs", formula="本期利润表营业成本", evidence_ids=_merge_evidence(facts["cogs"], prior_facts["cogs"]))
    add(
        "gross_profit",
        "毛利",
        "gross_profit",
        "gross_profit",
        formula="营业收入 − 营业成本",
        evidence_ids=_merge_evidence(facts["revenue"], facts["cogs"], prior_facts["revenue"], prior_facts["cogs"]),
    )
    add(
        "gross_margin",
        "毛利率",
        "gross_margin",
        "gross_margin",
        unit="%",
        change_type="pp",
        formula="毛利 ÷ 营业收入 × 100",
        evidence_ids=_merge_evidence(facts["revenue"], facts["cogs"], prior_facts["revenue"], prior_facts["cogs"]),
    )
    add(
        "selling_expenses",
        "销售费用",
        "selling_expenses",
        "selling_expenses",
        formula="本期利润表销售费用",
        evidence_ids=_merge_evidence(facts["selling_expenses"], prior_facts["selling_expenses"]),
        limitation="费用明细存在缺失项目，费用下降原因尚不能由明细充分验证。",
    )
    add(
        "selling_expense_ratio",
        "销售费用率",
        "selling_expense_ratio",
        "selling_expense_ratio",
        unit="%",
        change_type="pp",
        formula="销售费用 ÷ 营业收入 × 100",
        evidence_ids=_merge_evidence(facts["selling_expenses"], facts["revenue"], prior_facts["selling_expenses"], prior_facts["revenue"]),
        limitation="明细缺失，费用率变化的原因仍需核验。",
    )
    add(
        "net_profit",
        "净利润",
        "net_profit",
        "net_profit",
        formula="利润表净利润",
        evidence_ids=_merge_evidence(facts["net_profit"], prior_facts["net_profit"]),
    )
    add(
        "net_margin",
        "净利率",
        "net_margin",
        "net_margin",
        unit="%",
        change_type="pp",
        formula="净利润 ÷ 营业收入 × 100",
        evidence_ids=_merge_evidence(facts["net_profit"], facts["revenue"], prior_facts["net_profit"], prior_facts["revenue"]),
    )
    add(
        "operating_cash_flow",
        "经营活动现金流",
        "operating_cash_flow",
        "operating_cash_flow",
        formula="现金流量表经营活动现金流量净额",
        evidence_ids=_merge_evidence(facts["operating_cash_flow"], prior_facts["operating_cash_flow"]),
    )
    add(
        "ocf_to_net_profit",
        "OCF / 净利润",
        "ocf_to_net_profit",
        "ocf_to_net_profit",
        unit="x",
        change_type="absolute",
        formula="经营活动现金流量净额 ÷ 净利润",
        evidence_ids=_merge_evidence(facts["operating_cash_flow"], facts["net_profit"], prior_facts["operating_cash_flow"], prior_facts["net_profit"]),
        limitation="单期低于 0.8 不等于连续两期触发规则；连续条件仍需历史期间数据。",
    )
    add(
        "accounts_receivable",
        "应收账款",
        "accounts_receivable",
        "accounts_receivable",
        formula="资产负债表期末应收账款",
        evidence_ids=_merge_evidence(facts["accounts_receivable"], prior_facts["accounts_receivable"]),
    )
    add(
        "ar_growth",
        "应收账款同比",
        "ar_growth",
        None,
        unit="%",
        change_type="absolute",
        formula="（期末应收账款 − 上年同期）/ 上年同期 × 100",
        evidence_ids=_merge_evidence(facts["accounts_receivable"], prior_facts["accounts_receivable"]),
        delta=ar_growth,
    )
    add(
        "ar_turnover_days",
        "应收账款周转天数",
        "ar_turnover_days",
        "ar_turnover_days",
        unit="天",
        change_type="absolute",
        formula=f"期末应收账款 ÷ 营业收入 × {days} 天（期末口径）" if days else "期末应收账款 ÷ 营业收入 × 期间天数",
        evidence_ids=_merge_evidence(facts["accounts_receivable"], facts["revenue"], prior_facts["accounts_receivable"], prior_facts["revenue"]),
        limitation="使用期末口径，未使用平均余额；期间天数按本期实际日历天数计算。",
    )
    add(
        "other_receivables",
        "其他应收款",
        "other_receivables",
        "other_receivables",
        formula="资产负债表期末其他应收款",
        evidence_ids=_merge_evidence(facts["other_receivables"], prior_facts["other_receivables"]),
    )
    add(
        "other_receivables_ratio",
        "其他应收款 / 总资产",
        "other_receivables_ratio",
        "other_receivables_ratio",
        unit="%",
        change_type="pp",
        formula="其他应收款 ÷ 总资产 × 100",
        evidence_ids=_merge_evidence(facts["other_receivables"], facts["total_assets"], prior_facts["other_receivables"], prior_facts["total_assets"]),
    )
    add(
        "inventory",
        "存货",
        "inventory",
        "inventory",
        formula="资产负债表期末存货",
        evidence_ids=_merge_evidence(facts["inventory"], prior_facts["inventory"]),
    )
    inventory_metric = add(
        "inventory_turnover_days",
        "存货周转天数",
        "inventory_turnover_days",
        None,
        unit="天",
        formula="平均存货 ÷ 营业成本 × 期间天数",
        status="not_computable",
        status_label="不可计算",
        evidence_ids=_merge_evidence(facts["inventory"], facts["cogs"]),
        limitation="缺少期初/平均存货余额，未用期末余额替代，也未补 0。",
        note="缺少平均存货余额",
    )
    inventory_metric.value = None
    inventory_metric.prior = None
    add(
        "working_capital_drag",
        "营运资本净占用",
        "working_capital_drag",
        None,
        unit="CNY",
        change_type="absolute",
        formula="应收账款增加 + 其他应收款增加 + 存货增加 − 应付账款增加",
        evidence_ids=_merge_evidence(
            facts["accounts_receivable"],
            facts["other_receivables"],
            facts["inventory"],
            facts["accounts_payable"],
            prior_facts["accounts_receivable"],
            prior_facts["other_receivables"],
            prior_facts["inventory"],
            prior_facts["accounts_payable"],
        ),
        status="computed" if working_capital["complete"] else "not_computable",
        status_label="可计算" if working_capital["complete"] else "不可计算",
        limitation="" if working_capital["complete"] else "部分资产负债项目缺失，无法完整拆解营运资本。",
    )
    add(
        "september_revenue_share",
        f"{monthly.get('last_month_label', '期末月')}收入占季度比",
        "september_revenue_share",
        "september_revenue_share",
        unit="%",
        change_type="pp",
        formula="期末月收入 ÷ 当季三个月收入合计 × 100",
        evidence_ids=_merge_evidence(
            monthly.get("current_last_month"),
            *[fact for fact in monthly.get("current_month_facts", []) if fact],
            monthly.get("prior_last_month"),
            *[fact for fact in monthly.get("prior_month_facts", []) if fact],
        ),
        status="computed" if monthly.get("current_share_pct") is not None else "not_computable",
        status_label="可计算" if monthly.get("current_share_pct") is not None else "不可计算",
        limitation="" if monthly.get("current_share_pct") is not None else "月度收入数据不足，无法计算季末月集中度。",
    )

    # Remove metrics whose value is None but that are designed to be not computable;
    # keep them for visible empty states. Derived values are never converted to zero.
    return AnalysisContext(
        dataset=dataset,
        current_period=current_period,
        prior_period=prior_period,
        current_balance_date=current_end,
        prior_balance_date=prior_end,
        days=days,
        values={**values, "working_capital_changes": working_capital["changes"]},
        metrics=metrics,
        facts={**facts, **{f"prior_{key}": value for key, value in prior_facts.items()}},
        monthly={**monthly, "working_capital": working_capital},
    )
