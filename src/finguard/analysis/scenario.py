from __future__ import annotations

from decimal import Decimal

from finguard.analysis.formatting import dec, fmt_wan
from finguard.analysis.metrics import AnalysisContext
from finguard.models import ScenarioBaseline, ScenarioParams, ScenarioResult

SCENARIO_FIELDS = (
    "revenue",
    "gross_margin",
    "selling_expenses",
    "admin_expenses",
    "rd_expenses",
    "finance_expenses",
    "other_income_net",
    "income_tax",
    "net_profit",
)


class ScenarioUnavailable(ValueError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("情景模拟缺少可追溯的基础数据：" + "、".join(missing))


def build_baseline(context: AnalysisContext) -> ScenarioBaseline:
    missing: list[str] = []
    for field in SCENARIO_FIELDS:
        if context.values.get(field) is None:
            missing.append(field)
    if missing:
        labels = {
            "revenue": "营业收入",
            "gross_margin": "毛利率",
            "selling_expenses": "销售费用",
            "admin_expenses": "管理费用",
            "rd_expenses": "研发费用",
            "finance_expenses": "财务费用",
            "other_income_net": "其他净收益",
            "income_tax": "所得税费用",
            "net_profit": "净利润",
        }
        raise ScenarioUnavailable([labels.get(field, field) for field in missing])

    revenue = dec(context.values["revenue"])
    gross_margin_pct = dec(context.values["gross_margin"])
    gross_margin = gross_margin_pct / Decimal(100)
    selling = dec(context.values["selling_expenses"])
    admin = dec(context.values["admin_expenses"])
    rd = dec(context.values["rd_expenses"])
    finance = dec(context.values["finance_expenses"])
    other = dec(context.values["other_income_net"])
    income_tax = dec(context.values["income_tax"])
    net_profit_actual = dec(context.values["net_profit"])
    pretax_actual = net_profit_actual + income_tax
    tax_rate = income_tax / pretax_actual if pretax_actual else Decimal("0.25")
    gross_profit = revenue * gross_margin
    pretax_calc = gross_profit - selling - admin - rd - finance + other
    net_calc = pretax_calc * (Decimal(1) - tax_rate)
    evidence_ids: list[str] = []
    for field in SCENARIO_FIELDS:
        fact = context.facts.get(field)
        if fact is not None:
            evidence_id = f"EV-{fact.id}"
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
    limitations = [
        "管理费用、研发费用、财务费用与其他净收益按当前报表金额固定不变。",
        "所得税率按本期所得税费用 / 利润总额推导，未考虑递延所得税与税收优惠变化。",
        "未纳入营运资本、产能、价格、产品结构、汇率、融资和市场约束。",
        "模拟不是预算、盈利预测或投资建议。",
    ]
    if abs(net_calc - net_profit_actual) > Decimal("1"):
        limitations.append(
            f"由利润表分项推导的基准净利润为 {fmt_wan(net_calc)}，与报表净利润 {fmt_wan(net_profit_actual)} 存在差异，需复核一次性项目或分类。"
        )
    return ScenarioBaseline(
        revenue=float(revenue),
        gross_margin_pct=float(gross_margin_pct),
        selling_expenses=float(selling),
        admin_expenses=float(admin),
        rd_expenses=float(rd),
        finance_expenses=float(finance),
        other_income_net=float(other),
        income_tax=float(income_tax),
        tax_rate_pct=float(tax_rate * 100),
        net_profit=float(net_profit_actual),
        net_margin_pct=float(net_profit_actual / revenue * 100) if revenue else 0.0,
        source_fields=[
            "营业收入",
            "毛利率（由利润表计算）",
            "销售费用",
            "管理费用",
            "研发费用",
            "财务费用",
            "其他净收益",
            "所得税费用",
            "净利润",
        ],
        evidence_ids=evidence_ids,
        limitations=limitations,
    )


def run_scenario(context: AnalysisContext, params: ScenarioParams) -> ScenarioResult:
    baseline = build_baseline(context)
    revenue0 = dec(baseline.revenue)
    margin0 = dec(baseline.gross_margin_pct) / Decimal(100)
    selling0 = dec(baseline.selling_expenses)
    admin0 = dec(baseline.admin_expenses)
    rd0 = dec(baseline.rd_expenses)
    finance0 = dec(baseline.finance_expenses)
    other0 = dec(baseline.other_income_net)
    tax_rate = dec(baseline.tax_rate_pct) / Decimal(100)

    revenue = revenue0 * (Decimal(1) + dec(params.revenue_change_pct) / Decimal(100))
    margin = margin0 - dec(params.cost_rate_change_pp) / Decimal(100)
    margin = max(Decimal("0.02"), min(Decimal("0.92"), margin))
    gross_profit = revenue * margin
    selling = selling0 * (Decimal(1) + dec(params.selling_expense_change_pct) / Decimal(100))
    pretax = gross_profit - selling - admin0 - rd0 - finance0 + other0
    net = pretax * (Decimal(1) - tax_rate)
    delta_amount = net - dec(baseline.net_profit)
    delta_pct = delta_amount / abs(dec(baseline.net_profit)) * 100 if baseline.net_profit else Decimal(0)
    net_margin = net / revenue * 100 if revenue else Decimal(0)

    formula = (
        "模拟营业收入 = 基准营业收入 × (1 + 收入变化率)\n"
        "模拟毛利率 = 基准毛利率 − 成本率变化(pp) / 100\n"
        "模拟毛利 = 模拟营业收入 × 模拟毛利率\n"
        "模拟销售费用 = 基准销售费用 × (1 + 销售费用变化率)\n"
        "模拟利润总额 = 模拟毛利 − 模拟销售费用 − 管理费用 − 研发费用 − 财务费用 + 其他净收益\n"
        "模拟净利润 = 模拟利润总额 × (1 − 所得税率)"
    )
    return ScenarioResult(
        params=params,
        baseline=baseline,
        revenue=float(revenue),
        gross_margin_pct=float(margin * 100),
        gross_profit=float(gross_profit),
        selling_expenses=float(selling),
        pretax_profit=float(pretax),
        net_profit=float(net),
        net_margin_pct=float(net_margin),
        delta_amount=float(delta_amount),
        delta_pct=float(delta_pct),
        formula=formula,
        assumptions=[
            f"收入变化 {params.revenue_change_pct:+.1f}%",
            f"成本率变化 {params.cost_rate_change_pp:+.1f}pp",
            f"销售费用变化 {params.selling_expense_change_pct:+.1f}%",
            f"管理费用固定为 {fmt_wan(admin0)}",
            f"研发费用固定为 {fmt_wan(rd0)}",
            f"财务费用固定为 {fmt_wan(finance0)}",
            f"其他净收益固定为 {fmt_wan(other0)}",
            f"所得税率 {baseline.tax_rate_pct:.2f}%",
        ],
        limitations=baseline.limitations,
        is_simulation=True,
    )
