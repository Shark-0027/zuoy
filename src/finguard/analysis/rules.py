from __future__ import annotations

from decimal import Decimal

from finguard.analysis.formatting import fmt_pct, fmt_pp, fmt_ratio, fmt_wan
from finguard.analysis.metrics import AnalysisContext
from finguard.models import Fact, Finding

RULE_VERSION = "v0.2"


def _metric_value(context: AnalysisContext, metric_id: str) -> Decimal | None:
    metric = context.metric(metric_id)
    return metric.value if metric else None


def _metric_prior(context: AnalysisContext, metric_id: str) -> Decimal | None:
    metric = context.metric(metric_id)
    return metric.prior if metric else None


def _raw_fact(fact: Fact | None) -> dict[str, str]:
    if fact is None:
        return {
            "period": "缺失",
            "item": "—",
            "value": "—",
            "source": "未找到可追溯数据",
        }
    return {
        "period": fact.period_raw,
        "item": fact.raw_item,
        "value": fact.raw_value + (f" {fact.unit_raw}" if fact.unit_raw else ""),
        "source": f"{fact.source_file} · 第 {fact.source_row} 行",
    }


def _evidence(context: AnalysisContext, fields: tuple[str, ...]) -> list[str]:
    ids: list[str] = []
    for field in fields:
        for source in (context.facts.get(field), context.facts.get(f"prior_{field}")):
            if source is not None:
                evidence_id = f"EV-{source.id}"
                if evidence_id not in ids:
                    ids.append(evidence_id)
    return ids


def _status_label(status: str) -> str:
    return {
        "triggered": "已触发",
        "candidate": "候选 · 待核验",
        "needs_evidence": "待补证",
        "insufficient_data": "证据不足",
        "not_triggered": "未触发",
    }.get(status, "待核验")


def _finding(
    *,
    finding_id: str,
    title: str,
    risk_level: str,
    status: str,
    rule_id: str,
    rule_name: str,
    rule_condition: str,
    period: str,
    metric_label: str,
    metric_value: str,
    metric_sub: str,
    evidence_summary: str,
    recommendation: str,
    limitation: str,
    raw_data: list[dict[str, str]],
    formula: str,
    basis: str,
    evidence_ids: list[str],
    missing_evidence: list[str] | None = None,
) -> Finding:
    return Finding(
        id=finding_id,
        title=title,
        risk_level=risk_level,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        status_label=_status_label(status),
        rule_id=rule_id,
        rule_name=rule_name,
        rule_condition=rule_condition,
        period=period,
        metric_label=metric_label,
        metric_value=metric_value,
        metric_sub=metric_sub,
        evidence_summary=evidence_summary,
        recommendation=recommendation,
        limitation=limitation,
        raw_data=raw_data,
        formula=formula,
        basis=basis,
        evidence_ids=evidence_ids,
        missing_evidence=missing_evidence or [],
    )


def detect_findings(context: AnalysisContext) -> list[Finding]:
    findings: list[Finding] = []
    period = context.period_label
    prior_label = context.prior_period_label
    revenue_growth = context.metric("revenue").delta if context.metric("revenue") else None
    ar_growth = _metric_value(context, "ar_growth")
    cogs_growth = context.metric("cogs").delta if context.metric("cogs") else None
    gross_margin = _metric_value(context, "gross_margin")
    prior_gross_margin = _metric_prior(context, "gross_margin")
    gross_margin_delta = context.metric("gross_margin").delta if context.metric("gross_margin") else None
    selling_ratio = _metric_value(context, "selling_expense_ratio")
    prior_selling_ratio = _metric_prior(context, "selling_expense_ratio")
    selling_ratio_delta = context.metric("selling_expense_ratio").delta if context.metric("selling_expense_ratio") else None
    ocf_ratio = _metric_value(context, "ocf_to_net_profit")
    prior_ocf_ratio = _metric_prior(context, "ocf_to_net_profit")
    sep_share = _metric_value(context, "september_revenue_share")
    prior_sep_share = _metric_prior(context, "september_revenue_share")
    sep_delta = context.metric("september_revenue_share").delta if context.metric("september_revenue_share") else None
    or_metric = context.metric("other_receivables")
    or_growth = or_metric.delta if or_metric and or_metric.delta is not None else None
    or_ratio = _metric_value(context, "other_receivables_ratio")

    # AR-01: one-period signal is a candidate. Continuity is not claimed without history.
    if (
        ar_growth is not None
        and revenue_growth is not None
        and (ar_growth - revenue_growth) >= Decimal("20")
    ):
        gap = ar_growth - revenue_growth
        ar_fact = context.facts.get("accounts_receivable")
        prior_ar = context.facts.get("prior_accounts_receivable")
        revenue_fact = context.facts.get("revenue")
        prior_revenue = context.facts.get("prior_revenue")
        findings.append(
            _finding(
                finding_id="A-01",
                title="应收账款增速显著高于营业收入（单期信号）",
                risk_level="medium",
                status="candidate",
                rule_id="AR-01",
                rule_name="应收增速差",
                rule_condition="应收账款同比增速 − 营业收入同比增速 ≥ 20pp，且连续 2 个期间；连续条件本次未验证。",
                period=f"{period} / {prior_label}",
                metric_label="应收增速差",
                metric_value=f"{fmt_pct(ar_growth, signed=True)} vs {fmt_pct(revenue_growth, signed=True)}",
                metric_sub=f"差值 {fmt_pp(gap)}",
                evidence_summary=f"应收账款 {fmt_wan(ar_fact.value if ar_fact else None)}，同比 {fmt_pct(ar_growth, signed=True)}；营业收入同比 {fmt_pct(revenue_growth, signed=True)}。",
                recommendation="获取应收账款账龄、前五大客户、期后回款与信用政策；补齐更早期间后复核连续两期条件。",
                limitation="当前仅有一次同比比较，无法证明连续两个期间满足阈值；因此只标记为中风险候选，不声称规则已完整触发。",
                raw_data=[
                    _raw_fact(ar_fact),
                    _raw_fact(prior_ar),
                    _raw_fact(revenue_fact),
                    _raw_fact(prior_revenue),
                ],
                formula="应收增速 = (期末应收 − 上年同期应收) / 上年同期应收 × 100；收入增速同理；差值 ≥ 20pp",
                basis=f"本期应收增速 {fmt_pct(ar_growth)}，收入增速 {fmt_pct(revenue_growth)}，单期差值 {fmt_pp(gap)}。连续两期数据未提供，因此按候选风险处理。",
                evidence_ids=_evidence(context, ("accounts_receivable", "revenue")),
                missing_evidence=["连续两期应收账款与营业收入数据", "账龄表", "前五大客户与期后回款"],
            )
        )

    # CF-01: only one period below 0.8; prior is 0.91. Candidate, not a completed rule.
    if ocf_ratio is not None and ocf_ratio < Decimal("0.8"):
        findings.append(
            _finding(
                finding_id="A-02",
                title="经营现金流 / 净利润低于阈值（单期，连续条件未验证）",
                risk_level="medium",
                status="candidate" if (prior_ocf_ratio is None or prior_ocf_ratio >= Decimal("0.8")) else "triggered",
                rule_id="CF-01",
                rule_name="利润现金含量",
                rule_condition="经营活动现金流 / 净利润 < 0.80，且连续 2 个期间；本次仅本期低于阈值。",
                period=f"{period} / {prior_label}",
                metric_label="OCF / 净利润",
                metric_value=fmt_ratio(ocf_ratio),
                metric_sub=f"上年同期 {fmt_ratio(prior_ocf_ratio)}",
                evidence_summary=f"经营现金流 {fmt_wan(context.values.get('operating_cash_flow'))}，净利润 {fmt_wan(context.values.get('net_profit'))}；OCF/净利润 {fmt_ratio(ocf_ratio)}。",
                recommendation="拆解应收账款、其他应收款、存货与应付账款变动；补充更早期间并与客户回款、票据结算核对。",
                limitation="上年同期 OCF/净利润为 0.91，仅本期低于 0.8；现有数据不足以满足连续两个期间条件，不能升级为确定结论。",
                raw_data=[
                    _raw_fact(context.facts.get("operating_cash_flow")),
                    _raw_fact(context.facts.get("net_profit")),
                    _raw_fact(context.facts.get("prior_operating_cash_flow")),
                    _raw_fact(context.facts.get("prior_net_profit")),
                ],
                formula="OCF / 净利润 = 经营活动现金流量净额 ÷ 净利润；阈值 < 0.80",
                basis=f"本期 OCF/净利润 {fmt_ratio(ocf_ratio)}，上年同期 {fmt_ratio(prior_ocf_ratio)}。单期低于阈值只构成候选风险。",
                evidence_ids=_evidence(context, ("operating_cash_flow", "net_profit")),
                missing_evidence=["至少一个更早期间的 OCF 与净利润数据", "客户回款与票据结算明细"],
            )
        )

    # GM-01: one-period margin/cost divergence is sufficient for a triggered medium signal.
    if gross_margin_delta is not None and cogs_growth is not None and revenue_growth is not None:
        cogs_gap = cogs_growth - revenue_growth
        status = "triggered" if gross_margin_delta <= Decimal("-2") and cogs_gap >= Decimal("3") else "not_triggered"
        findings.append(
            _finding(
                finding_id="A-03",
                title="毛利率下降且成本增速高于收入",
                risk_level="medium",
                status=status,
                rule_id="GM-01",
                rule_name="毛利率收缩",
                rule_condition="毛利率同比下降 ≥ 2pp，且营业成本增速高于营业收入增速 3pp 以上。",
                period=f"{period} / {prior_label}",
                metric_label="毛利率变化",
                metric_value=fmt_pct(gross_margin),
                metric_sub=f"{fmt_pp(gross_margin_delta)} · 成本增速 {fmt_pct(cogs_growth, signed=True)}",
                evidence_summary=f"毛利率由 {fmt_pct(prior_gross_margin)} 降至 {fmt_pct(gross_margin)}；营业成本同比 {fmt_pct(cogs_growth, signed=True)}，收入同比 {fmt_pct(revenue_growth, signed=True)}。",
                recommendation="按产品、客户和成本项目拆分毛利，核对原材料价格、售价、返利与产品结构变化。",
                limitation="报表只有汇总成本，没有产品与客户明细，无法确认毛利率下降的单一原因。",
                raw_data=[
                    _raw_fact(context.facts.get("revenue")),
                    _raw_fact(context.facts.get("cogs")),
                    _raw_fact(context.facts.get("prior_revenue")),
                    _raw_fact(context.facts.get("prior_cogs")),
                ],
                formula="毛利率 = (营业收入 − 营业成本) / 营业收入 × 100；成本增速 − 收入增速",
                basis=f"毛利率变化 {fmt_pp(gross_margin_delta)}，成本增速差 {fmt_pp(cogs_gap)}。该单期条件已满足，但归因仍需明细。",
                evidence_ids=_evidence(context, ("revenue", "cogs")),
                missing_evidence=["产品线收入与成本拆分", "主要原材料价格与售价变动"],
            )
        )

    # EX-01: rate change is triggered; cause is blocked by missing detail.
    if selling_ratio_delta is not None and revenue_growth is not None and selling_ratio is not None:
        status = "needs_evidence" if (selling_ratio_delta <= Decimal("-2") and revenue_growth > 5) else "not_triggered"
        findings.append(
            _finding(
                finding_id="A-04",
                title="销售费用率下降，明细口径待核验",
                risk_level="medium",
                status=status,
                rule_id="EX-01",
                rule_name="费用率异动",
                rule_condition="销售费用率同比下降 > 2pp，且营业收入增长 > 5%。",
                period=f"{period} / {prior_label}",
                metric_label="销售费用率",
                metric_value=fmt_pct(selling_ratio),
                metric_sub=f"{fmt_pp(selling_ratio_delta)} · 明细存在缺失",
                evidence_summary=f"销售费用率由 {fmt_pct(prior_selling_ratio)} 降至 {fmt_pct(selling_ratio)}；费用明细存在缺失项目，未按 0 补齐。",
                recommendation="获取销售费用明细、费用重分类记录与合同；确认推广服务费、差旅费、运输费的归属。",
                limitation="费用率可由利润表计算，但费用下降原因不能由缺失明细充分验证，因此保留待补证状态。",
                raw_data=[
                    _raw_fact(context.facts.get("selling_expenses")),
                    _raw_fact(context.facts.get("prior_selling_expenses")),
                    _raw_fact(context.facts.get("revenue")),
                    _raw_fact(context.facts.get("prior_revenue")),
                ],
                formula="销售费用率 = 销售费用 / 营业收入 × 100；同比变化以 pp 计",
                basis=f"费用率变化 {fmt_pp(selling_ratio_delta)}，收入增速 {fmt_pct(revenue_growth, signed=True)}。规则条件满足，但明细证据不足。",
                evidence_ids=_evidence(context, ("selling_expenses", "revenue")),
                missing_evidence=["销售费用差旅费与运输费明细", "费用重分类记录"],
            )
        )

    # REV-01: computed from actual monthly revenue. This fixes the old 48.7% error.
    if sep_share is not None:
        status = "triggered" if sep_share > 45 and (sep_delta or Decimal(0)) > 10 else "not_triggered"
        month_label = context.monthly.get("last_month_label", "期末月")
        findings.append(
            _finding(
                finding_id="A-05",
                title=f"{month_label}收入占季度比重偏高",
                risk_level="medium",
                status=status,
                rule_id="REV-01",
                rule_name="收入时间集中",
                rule_condition="季末月收入 / 当季三个月收入 > 45%，且较上年同期上升 10pp 以上。",
                period=period,
                metric_label=f"{month_label}收入占比",
                metric_value=fmt_pct(sep_share),
                metric_sub=f"上年同期 {fmt_pct(prior_sep_share)} · {fmt_pp(sep_delta)}",
                evidence_summary=f"{month_label}收入 {fmt_wan(context.monthly.get('current_last_month').value if context.monthly.get('current_last_month') else None)}，占当季收入 {fmt_pct(sep_share)}；上年同期 {fmt_pct(prior_sep_share)}。",
                recommendation="抽取季末月大额收入，核对合同、发货、签收、验收与期后退货；检查收入截止性。",
                limitation="月度收入数据来自报表；未取得发货单、签收单和期后退货记录，不能仅凭占比判断提前确认收入。",
                raw_data=[
                    _raw_fact(context.monthly.get("current_last_month")),
                    *[_raw_fact(fact) for fact in context.monthly.get("current_month_facts", []) if fact],
                    _raw_fact(context.monthly.get("prior_last_month")),
                ],
                formula=f"{month_label}收入占比 = 期末月收入 ÷ 当季三个月收入合计 × 100；上年同期比较变化为 pp",
                basis=f"本期占比 {fmt_pct(sep_share)}，上年同期 {fmt_pct(prior_sep_share)}，变化 {fmt_pp(sep_delta)}；已按原始月度收入重新计算。",
                evidence_ids=[f"EV-{fact.id}" for fact in context.monthly.get("current_month_facts", []) if fact] + [f"EV-{fact.id}" for fact in context.monthly.get("prior_month_facts", []) if fact],
                missing_evidence=["季末月发货单、签收单、验收单", "期后退货与红字发票"],
            )
        )

    # OR-01: scale triggers, but the nature is missing; keep low + needs_evidence.
    if or_growth is not None and or_ratio is not None:
        status = "needs_evidence" if or_growth > 50 and or_ratio > 3 else "not_triggered"
        findings.append(
            _finding(
                finding_id="A-06",
                title="其他应收款规模快速上升，款项性质待补证",
                risk_level="low",
                status=status,
                rule_id="OR-01",
                rule_name="其他应收款异动",
                rule_condition="其他应收款增幅 > 50%，且占总资产 > 3%；对手方与性质未披露时降级为待补证。",
                period=f"{period} / {prior_label}",
                metric_label="其他应收款同比",
                metric_value=fmt_pct(or_growth, signed=True),
                metric_sub=f"占总资产 {fmt_pct(or_ratio)}",
                evidence_summary=f"其他应收款 {fmt_wan(context.values.get('other_receivables'))}，同比 {fmt_pct(or_growth, signed=True)}，占总资产 {fmt_pct(or_ratio)}；对手方与性质未披露。",
                recommendation="获取其他应收款明细：对手方、性质、账龄、回收计划与关联方清单。",
                limitation="缺对手方与款项性质，无法区分押金、备用金、预付款或资金占用；因此不升级为确定结论。",
                raw_data=[
                    _raw_fact(context.facts.get("other_receivables")),
                    _raw_fact(context.facts.get("prior_other_receivables")),
                    _raw_fact(context.facts.get("total_assets")),
                    _raw_fact(context.facts.get("prior_total_assets")),
                ],
                formula="其他应收款增速 = (期末 − 上年同期) / 上年同期；占总资产 = 其他应收款 / 总资产",
                basis=f"增幅 {fmt_pct(or_growth, signed=True)}，占总资产 {fmt_pct(or_ratio)}；规模条件满足，但性质证据不足。",
                evidence_ids=_evidence(context, ("other_receivables", "total_assets")),
                missing_evidence=["其他应收款对手方与款项性质", "账龄与回收计划"],
            )
        )

    # Rules that explicitly evaluated to not_triggered are audit trace data, not
    # user-facing anomalies. Omit them from the finding list.
    return [finding for finding in findings if finding.status != "not_triggered"]
