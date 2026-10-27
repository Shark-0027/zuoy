from __future__ import annotations

from finguard.analysis.formatting import fmt_pct, fmt_pp, fmt_ratio, fmt_wan, metric_display
from finguard.analysis.metrics import AnalysisContext
from finguard.models import Dataset, Evidence, Finding, PendingQuestion


def build_evidence(dataset: Dataset) -> list[Evidence]:
    evidence: list[Evidence] = []
    for fact in dataset.facts:
        note_parts = []
        if fact.status != "valid":
            note_parts.append(f"数据状态：{fact.status}")
        if fact.note:
            note_parts.append(fact.note)
        normalized = fmt_wan(fact.value) if fact.value is not None else ""
        evidence.append(
            Evidence(
                id=f"EV-{fact.id}",
                label=f"{fact.source_file} · {fact.raw_item}",
                source_file=fact.source_file,
                source_sheet=fact.source_sheet,
                source_row=fact.source_row,
                raw_item=fact.raw_item,
                period=fact.period_raw,
                raw_value=f"{fact.raw_value} {fact.unit_raw}".strip(),
                normalized_value=normalized,
                formula="原始报表行",
                result=normalized,
                note="；".join(note_parts),
                evidence_type="fact",
                fact_id=fact.id,
            )
        )
    return evidence


def revenue_profit_analysis(context: AnalysisContext) -> dict[str, object]:
    revenue = context.metric("revenue")
    cogs = context.metric("cogs")
    gross = context.metric("gross_margin")
    net = context.metric("net_profit")
    return {
        "title": "收入与利润变化",
        "period": context.period_label,
        "prior_period": context.prior_period_label,
        "revenue_growth_pct": float(revenue.delta) if revenue and revenue.delta is not None else None,
        "cogs_growth_pct": float(cogs.delta) if cogs and cogs.delta is not None else None,
        "gross_margin_pct": float(gross.value) if gross and gross.value is not None else None,
        "gross_margin_change_pp": float(gross.delta) if gross and gross.delta is not None else None,
        "net_profit_growth_pct": float(net.delta) if net and net.delta is not None else None,
        "summary": (
            f"营业收入同比 {fmt_pct(revenue.delta, signed=True) if revenue else '—'}，"
            f"营业成本同比 {fmt_pct(cogs.delta, signed=True) if cogs else '—'}；"
            f"毛利率 {fmt_pct(gross.value) if gross else '—'}，同比 {fmt_pp(gross.delta) if gross else '—'}；"
            f"净利润同比 {fmt_pct(net.delta, signed=True) if net else '—'}。"
        ),
    }


def cash_quality_analysis(context: AnalysisContext) -> dict[str, object]:
    ocf = context.metric("operating_cash_flow")
    ratio = context.metric("ocf_to_net_profit")
    drag = context.metric("working_capital_drag")
    working = context.monthly.get("working_capital", {})
    changes = working.get("changes", {}) if isinstance(working, dict) else {}
    change_summary = "；".join(
        f"{label} {fmt_wan(changes.get(field))}"
        for field, label in (
            ("accounts_receivable", "应收账款变化"),
            ("other_receivables", "其他应收款变化"),
            ("inventory", "存货变化"),
            ("accounts_payable", "应付账款变化"),
        )
        if changes.get(field) is not None
    )
    return {
        "title": "经营现金流质量",
        "operating_cash_flow_change_pct": float(ocf.delta) if ocf and ocf.delta is not None else None,
        "ocf_to_net_profit": float(ratio.value) if ratio and ratio.value is not None else None,
        "working_capital_drag": float(drag.value) if drag and drag.value is not None else None,
        "working_capital_changes": {key: float(value) if value is not None else None for key, value in changes.items()},
        "summary": (
            f"经营现金流同比 {fmt_pct(ocf.delta, signed=True) if ocf else '—'}，"
            f"OCF/净利润 {fmt_ratio(ratio.value) if ratio else '—'}；"
            f"营运资本净占用 {fmt_wan(drag.value) if drag else '—'}。"
            + (f" {change_summary}。" if change_summary else "")
        ),
    }


def build_pending_questions(
    dataset: Dataset,
    context: AnalysisContext,
    findings: list[Finding],
) -> list[PendingQuestion]:
    questions: list[PendingQuestion] = []
    seen: set[str] = set()

    def add(title: str, detail: str, missing: str, action: str, severity: str = "warning", evidence_ids: list[str] | None = None) -> None:
        if title in seen:
            return
        seen.add(title)
        questions.append(
            PendingQuestion(
                id=f"Q-{len(questions) + 1:02d}",
                title=title,
                detail=detail,
                severity=severity,
                missing=missing,
                action=action,
                evidence_ids=evidence_ids or [],
            )
        )

    blockers = [issue for issue in dataset.quality if issue.blocking]
    for issue in blockers:
        add(
            "阻塞数据问题",
            issue.message,
            issue.detail or "需要补充或修正原始数据",
            "重新上传包含完整字段和期间的文件，再创建分析。",
            severity="blocker",
        )

    for finding in findings:
        if finding.rule_id in {"AR-01", "CF-01"} and finding.status in {"candidate", "insufficient_data"}:
            add(
                "连续期间条件未验证",
                "当前只有一次同比比较，不能证明规则要求的连续两个期间条件；候选风险不会升级为确定结论。",
                "补充至少一个更早期间的报表数据，或提供可核对的月度/季度数据。",
                "如确认使用当前限制继续，系统将保留“候选 · 待核验”标记；否则请补充数据后重新分析。",
                evidence_ids=finding.evidence_ids,
            )
        if finding.rule_id == "GM-01" and finding.status == "triggered":
            add(
                "毛利率下降原因缺产品拆分",
                "毛利率收缩条件已经由报表计算确认，但缺少产品、客户与成本项目明细，无法确认单一原因。",
                "按产品线和客户拆分的收入、成本与毛利，以及主要原材料价格与售价变化。",
                "如确认使用当前限制继续，报告会把毛利率变化列为已计算事实，把原因列为未验证。",
                evidence_ids=finding.evidence_ids,
            )
        if finding.rule_id == "EX-01":
            add(
                "销售费用明细缺失",
                "利润表销售费用可计算，但差旅费、运输费等明细为空，不能确认费用率下降的原因。",
                "完整的销售费用科目明细、重分类记录与相关合同。",
                "如确认使用当前限制继续，费用下降原因将在报告中标注为未验证。",
                evidence_ids=finding.evidence_ids,
            )
        if finding.rule_id == "REV-01":
            add(
                "季末收入截止性资料缺失",
                "季末月收入占比已按原始月度收入重算，但缺少发货、签收与退货资料，不能判断是否存在提前确认收入。",
                "季末大额收入的合同、发货单、签收单、验收单与期后退货记录。",
                "如确认使用当前限制继续，报告将仅把收入集中列为核查信号。",
                evidence_ids=finding.evidence_ids,
            )
        if finding.rule_id == "OR-01":
            add(
                "其他应收款性质未披露",
                "其他应收款规模条件已达到，但对手方、款项性质和账龄未知。",
                "其他应收款明细、对手方、关联方清单、账龄与回收计划。",
                "如确认使用当前限制继续，该事项保持低风险/待补证，不升级为确定结论。",
                evidence_ids=finding.evidence_ids,
            )

    inventory_metric = context.metric("inventory_turnover_days")
    if inventory_metric is not None and inventory_metric.status == "not_computable":
        add(
            "存货平均余额缺失",
            "缺少期初或平均存货余额，存货周转天数保持不可计算；系统没有用期末存货替代，也没有补 0。",
            "期初存货余额、平均存货或存货库龄明细。",
            "如确认使用当前限制继续，报告会保留“不可计算”状态。",
            evidence_ids=inventory_metric.evidence_ids,
        )

    key_metric_ids = (
        "revenue",
        "cogs",
        "gross_margin",
        "net_profit",
        "operating_cash_flow",
        "ocf_to_net_profit",
        "accounts_receivable",
        "ar_growth",
        "other_receivables",
        "working_capital_drag",
        "september_revenue_share",
    )
    unavailable = [
        metric
        for metric_id in key_metric_ids
        if (metric := context.metric(metric_id)) is not None and metric.status != "computed"
    ]
    if unavailable:
        labels = "、".join(metric.label for metric in unavailable)
        limitations = "；".join(metric.limitation or metric.note or "缺少可追溯数据" for metric in unavailable)
        add(
            "关键指标不可计算",
            f"{labels} 无法由当前数据计算。Agent 不会用 0 或猜测值替代。{limitations}",
            "补充缺失的科目、单位或对比期间。",
            "如确认使用当前限制继续，报告会保留不可计算状态；否则请补充数据后重新分析。",
            severity="blocker" if any(metric.id in {"revenue", "cogs", "net_profit", "operating_cash_flow"} for metric in unavailable) else "warning",
            evidence_ids=[evidence_id for metric in unavailable for evidence_id in metric.evidence_ids],
        )

    for issue in dataset.quality:
        if issue.code == "DQ-COMPARATIVE-PERIOD-MISSING" and not any("连续期间" in question.title for question in questions):
            add(
                "跨期比较期间不足",
                "当前数据只支持有限的同比比较。",
                "至少两个连续的可比期间。",
                "如确认使用当前限制继续，报告将标注候选风险。",
                evidence_ids=[],
            )
    return questions


def build_evidence_review_summary(findings: list[Finding], questions: list[PendingQuestion]) -> str:
    candidate = len([finding for finding in findings if finding.status == "candidate"])
    pending_evidence = len([finding for finding in findings if finding.status == "needs_evidence"])
    return (
        f"已核验 {len(findings)} 条规则结果；其中候选风险 {candidate} 条、待补证 {pending_evidence} 条。"
        f"发现 {len(questions)} 项需要人工确认的证据边界，分析在证据核验步骤暂停。"
    )


def build_step_summary(step_id: str, context: AnalysisContext, findings: list[Finding], questions: list[PendingQuestion]) -> str:
    if step_id == "revenue_profit":
        data = revenue_profit_analysis(context)
        return str(data["summary"])
    if step_id == "cash_quality":
        data = cash_quality_analysis(context)
        return str(data["summary"])
    if step_id == "anomalies":
        high = len([finding for finding in findings if finding.risk_level == "high"])
        medium = len([finding for finding in findings if finding.risk_level == "medium"])
        low = len([finding for finding in findings if finding.risk_level == "low"])
        return f"命中 {len(findings)} 条规则结果：高风险 {high}、中风险 {medium}、低风险/待补证 {low}。"
    if step_id == "evidence":
        return build_evidence_review_summary(findings, questions)
    return ""


def verify_report_text(markdown: str, state, findings: list[Finding], context: AnalysisContext) -> list[str]:
    errors: list[str] = []
    required = [
        "## 01 执行摘要",
        "## 02 企业与数据范围",
        "## 03 关键财务指标",
        "## 05 风险发现",
        "## 06 证据与计算过程",
        "## 08 Agent 实际执行过程",
        "## 10 分析限制",
    ]
    for heading in required:
        if heading not in markdown:
            errors.append(f"报告缺少章节：{heading}")
    for finding in findings:
        if finding.id not in markdown:
            errors.append(f"报告缺少发现引用：{finding.id}")
    banned = ["存在舞弊", "确认舞弊", "系虚构收入", "管理层侵占", "构成违法"]
    for phrase in banned:
        if phrase in markdown:
            errors.append(f"报告出现未证实定性短语：{phrase}")
    for metric_id in ("revenue", "cogs", "gross_margin", "net_profit", "operating_cash_flow"):
        metric = context.metric(metric_id)
        if metric and metric.status == "computed" and metric.value is not None:
            display = metric_display(metric)["value"]
            if display not in markdown:
                errors.append(f"报告未包含 {metric.label} 的计算结果 {display}")
    if state.scenario and state.scenario.net_profit:
        expected = fmt_wan(state.scenario.net_profit)
        if expected not in markdown:
            errors.append(f"报告未包含情景模拟结果 {expected}")
    return errors
