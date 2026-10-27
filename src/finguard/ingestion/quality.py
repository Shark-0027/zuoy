from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from finguard.ingestion.fields import CANONICAL_LABELS
from finguard.models import Dataset, Fact, QualityIssue

CORE_FLOW_FIELDS = ("revenue", "cogs", "net_profit", "operating_cash_flow")
BALANCE_FIELDS = (
    "accounts_receivable",
    "other_receivables",
    "inventory",
    "accounts_payable",
    "total_assets",
    "total_liabilities",
    "equity",
)
SCENARIO_FIELDS = (
    "selling_expenses",
    "admin_expenses",
    "rd_expenses",
    "finance_expenses",
    "other_income_net",
    "income_tax",
)


def _issue(
    code: str,
    severity: str,
    category: str,
    message: str,
    *,
    detail: str = "",
    impact: str = "",
    field: str | None = None,
    period: str | None = None,
    source_file: str | None = None,
    source_row: int | None = None,
    blocking: bool = False,
) -> QualityIssue:
    seed = f"{code}|{message}|{field}|{period}|{source_file}|{source_row}"
    return QualityIssue(
        id=f"DQ-{abs(hash(seed)) % 100000:05d}",
        code=code,
        severity=severity,  # type: ignore[arg-type]
        category=category,
        message=message,
        detail=detail,
        impact=impact,
        field=field,
        period=period,
        source_file=source_file,
        source_row=source_row,
        blocking=blocking,
    )


def build_quality_issues(dataset: Dataset) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    valid = [fact for fact in dataset.facts if fact.status == "valid" and fact.value is not None]
    fields = {fact.field for fact in valid}
    periods = {fact.period_raw for fact in valid}

    if not valid:
        issues.append(
            _issue(
                "DQ-NO-VALID-FACTS",
                "blocker",
                "source",
                "没有可计算的有效财务记录",
                detail="文件可能为空、结构无法识别、期间缺失或数值/单位无法解析。",
                impact="无法启动指标计算和 Agent 分析。",
                blocking=True,
            )
        )

    # Missing / invalid / unmapped source cells are surfaced individually.
    for fact in dataset.facts:
        if fact.status == "missing":
            issues.append(
                _issue(
                    "DQ-MISSING-VALUE",
                    "warning",
                    "missing",
                    f"“{fact.raw_item}”的金额为空",
                    detail="空值没有被当作 0，依赖该科目的指标可能无法计算。",
                    impact="相关指标将标记为不可计算或证据不足。",
                    field=fact.field,
                    period=fact.period_raw,
                    source_file=fact.source_file,
                    source_row=fact.source_row,
                )
            )
        elif fact.status == "invalid":
            issues.append(
                _issue(
                    "DQ-INVALID-VALUE",
                    "warning",
                    "invalid",
                    f"“{fact.raw_item}”的值无法解析或单位不明确",
                    detail=fact.note or "需要人工确认数值和单位。",
                    impact="该行不进入计算。",
                    field=fact.field,
                    period=fact.period_raw,
                    source_file=fact.source_file,
                    source_row=fact.source_row,
                )
            )
        elif fact.status == "unmapped":
            issues.append(
                _issue(
                    "DQ-UNMAPPED-ITEM",
                    "warning",
                    "mapping",
                    f"“{fact.raw_item}”未自动映射为标准字段",
                    detail=fact.note or "字段含义不明确，未猜测映射。",
                    impact="该行不会进入核心指标计算，需人工确认。",
                    field=fact.field,
                    period=fact.period_raw,
                    source_file=fact.source_file,
                    source_row=fact.source_row,
                )
            )

    for field in CORE_FLOW_FIELDS:
        if field not in fields:
            issues.append(
                _issue(
                    "DQ-CORE-FIELD-MISSING",
                    "blocker",
                    "required",
                    f"缺少核心字段：{CANONICAL_LABELS[field]}",
                    detail="核心指标和现金流分析需要该字段。",
                    impact="Agent 将无法完成完整分析。",
                    field=field,
                    blocking=True,
                )
            )

    missing_balance = [field for field in BALANCE_FIELDS if field not in fields]
    if missing_balance:
        issues.append(
            _issue(
                "DQ-BALANCE-FIELDS-MISSING",
                "warning",
                "required",
                "部分资产负债项目缺失",
                detail="、".join(CANONICAL_LABELS[field] for field in missing_balance),
                impact="应收、营运资本和资产占比指标可能不可计算。",
                blocking=False,
            )
        )

    missing_scenario = [field for field in SCENARIO_FIELDS if field not in fields]
    if missing_scenario:
        issues.append(
            _issue(
                "DQ-SCENARIO-FIELDS-MISSING",
                "warning",
                "scenario",
                "部分利润表情景参数缺失",
                detail="、".join(CANONICAL_LABELS[field] for field in missing_scenario),
                impact="情景模拟将被限制或不可用。",
                blocking=False,
            )
        )

    if not periods:
        issues.append(
            _issue(
                "DQ-NO-PERIOD",
                "blocker",
                "period",
                "没有识别到有效期间",
                detail="需要期间列或可从文件名识别的会计期间。",
                impact="无法执行跨期比较。",
                blocking=True,
            )
        )

    # Duplicate canonical facts (same period + field) with a conflicting value.
    seen: dict[tuple[str, str, str], list[Fact]] = defaultdict(list)
    for fact in valid:
        seen[(fact.statement, fact.period_raw, fact.field or "")].append(fact)
    for (_statement, period, field), facts in seen.items():
        if len(facts) > 1:
            values = {fact.value for fact in facts}
            if len(values) > 1:
                first = facts[0]
                issues.append(
                    _issue(
                        "DQ-DUPLICATE-FACT",
                        "warning",
                        "duplicate",
                        f"{CANONICAL_LABELS.get(field, field)} 在 {period} 存在重复且冲突的记录",
                        detail="；".join(f"{fact.source_file}:{fact.source_row}={fact.raw_value}" for fact in facts),
                        impact="程序默认使用第一条有效记录，需人工确认。",
                        field=field,
                        period=period,
                        source_file=first.source_file,
                        source_row=first.source_row,
                    )
                )

    # Balance sheet identity, for each balance period where all three values exist.
    by_period: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for fact in valid:
        if fact.statement == "balance" and fact.field in {"total_assets", "total_liabilities", "equity"}:
            by_period[fact.period_raw][fact.field] = fact.value  # type: ignore[assignment]
    for period, values in by_period.items():
        if all(field in values for field in ("total_assets", "total_liabilities", "equity")):
            assets = values["total_assets"]
            liabilities = values["total_liabilities"]
            equity = values["equity"]
            diff = assets - liabilities - equity
            ratio = abs(diff) / assets if assets else Decimal(0)
            if ratio > Decimal("0.005"):
                issues.append(
                    _issue(
                        "DQ-BALANCE-MISMATCH",
                        "blocker",
                        "balance",
                        f"{period} 资产负债表不平",
                        detail=f"资产 {assets:,.2f}，负债+权益 {liabilities + equity:,.2f}，差异 {diff:,.2f}。",
                        impact="资产负债相关指标暂停计算。",
                        period=period,
                        blocking=True,
                    )
                )

    # A reported selling-expense total with incomplete detail is a specific,
    # traceable warning: do not assume the missing detail lines are zero.
    detail_facts = [
        fact for fact in dataset.facts if fact.statement == "expense_detail" and fact.period_raw
    ]
    missing_detail = [fact for fact in detail_facts if fact.status == "missing"]
    if missing_detail:
        issues.append(
            _issue(
                "DQ-EXPENSE-DETAIL-MISSING",
                "warning",
                "evidence",
                "销售费用明细存在缺失项目",
                detail="、".join(f"{fact.period_raw} {fact.raw_item}" for fact in missing_detail),
                impact="费用率可以计算，但费用下降原因无法由明细充分验证。",
                blocking=False,
            )
        )

    # Period coverage in income/cashflow should include a comparable prior period.
    ytd_periods = sorted(
        {fact.period_raw for fact in valid if fact.statement in {"income", "cashflow"} and fact.period_kind in {"ytd", "quarter"}}
    )
    if len(ytd_periods) < 2:
        issues.append(
            _issue(
                "DQ-COMPARATIVE-PERIOD-MISSING",
                "warning",
                "period",
                "跨期比较期间不足",
                detail="当前至少需要本期与上年同期；连续两期规则还需要更多历史期间。",
                impact="同比指标可能不可计算，连续期间规则只能标记为候选。",
                blocking=False,
            )
        )

    # Source coverage: every company should have an income and a balance/cash source.
    source_types = {fact.statement for fact in valid}
    for statement, label in (("income", "利润表"), ("cashflow", "现金流量表")):
        if statement not in source_types:
            issues.append(
                _issue(
                    "DQ-SOURCE-MISSING",
                    "blocker" if statement == "income" else "warning",
                    "source",
                    f"未识别到{label}数据",
                    detail="文件可能未上传、表名无法识别或字段缺失。",
                    impact="相关模块不可用。",
                    blocking=statement == "income",
                )
            )

    # Stable order: blockers, warnings, info.
    order = {"blocker": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda issue: (order[issue.severity], issue.code, issue.message))
    return issues


def apply_quality(dataset: Dataset) -> Dataset:
    dataset.quality = build_quality_issues(dataset)
    dataset.updated_at = dataset.updated_at.__class__.now(dataset.updated_at.tzinfo) if dataset.updated_at.tzinfo else dataset.updated_at
    return dataset
