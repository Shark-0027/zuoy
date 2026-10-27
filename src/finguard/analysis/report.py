from __future__ import annotations

import html
from datetime import datetime

from finguard.analysis.formatting import (
    fmt_days,
    fmt_pct,
    fmt_pp,
    fmt_ratio,
    fmt_wan,
    metric_display,
)
from finguard.analysis.metrics import AnalysisContext
from finguard.models import AnalysisState, Dataset, Evidence, Finding, Metric, ScenarioResult

RISK_LABELS = {"high": "高风险", "medium": "中风险", "low": "低风险 / 待补证"}
RISK_CLASSES = {"high": "risk-high", "medium": "risk-medium", "low": "risk-low"}
STATUS_CLASSES = {"triggered": "status-warning", "candidate": "status-warning", "needs_evidence": "status-info", "not_triggered": "status-neutral"}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def quality_score(dataset: Dataset) -> int:
    blockers = len([issue for issue in dataset.quality if issue.blocking])
    warnings = len([issue for issue in dataset.quality if issue.severity == "warning" and not issue.blocking])
    return max(40, 100 - blockers * 20 - warnings * 3)


def _metric(context: AnalysisContext, metric_id: str) -> Metric | None:
    return context.metric(metric_id)


def build_conclusions(context: AnalysisContext, findings: list[Finding]) -> tuple[list[str], list[str], list[str]]:
    def metric(metric_id: str) -> Metric | None:
        return context.metric(metric_id)

    verified: list[str] = []
    revenue = metric("revenue")
    cogs = metric("cogs")
    gross = metric("gross_margin")
    net = metric("net_profit")
    ocf = metric("operating_cash_flow")
    ocf_ratio = metric("ocf_to_net_profit")
    ar = metric("accounts_receivable")
    ar_days = metric("ar_turnover_days")
    sep = metric("september_revenue_share")
    if revenue and revenue.delta is not None and cogs and cogs.delta is not None:
        verified.append(
            f"营业收入同比 {fmt_pct(revenue.delta, signed=True)}，营业成本同比 {fmt_pct(cogs.delta, signed=True)}；成本增速高于收入。"
        )
    if gross and gross.value is not None and gross.delta is not None:
        verified.append(
            f"毛利率为 {fmt_pct(gross.value)}，较上年同期 {fmt_pp(gross.delta)}。"
        )
    if net and net.delta is not None:
        verified.append(f"净利润同比 {fmt_pct(net.delta, signed=True)}。")
    if ocf and ocf.delta is not None and ocf_ratio and ocf_ratio.value is not None:
        verified.append(
            f"经营现金流同比 {fmt_pct(ocf.delta, signed=True)}；OCF/净利润为 {fmt_ratio(ocf_ratio.value)}。"
        )
    if ar and ar.delta is not None:
        verified.append(f"应收账款同比 {fmt_pct(ar.delta, signed=True)}。")
    if ar_days and ar_days.value is not None:
        verified.append(
            f"应收账款周转天数为 {fmt_days(ar_days.value)}（期末口径，按本期 {context.days} 天计算）。"
        )
    if sep and sep.value is not None and sep.delta is not None:
        verified.append(
            f"{context.monthly.get('last_month_label', '季末月')}收入占当季收入 {fmt_pct(sep.value)}，较上年同期 {fmt_pp(sep.delta)}。"
        )

    unverified: list[str] = []
    for finding in findings:
        if finding.status in {"candidate", "needs_evidence", "insufficient_data"}:
            unverified.append(f"{finding.title}：{finding.limitation}")
    if not unverified:
        unverified.append("未发现需要单独列示的候选事项；仍应结合业务资料复核。")

    cannot = [
        "现有数据不足以判断是否存在收入虚构、资金占用、现金流操纵或舞弊行为。",
        "单期异常信号不能替代连续期间验证、合同、发票、回款和业务实质核查。",
        "情景模拟只反映明确假设下的利润敏感度，不是预测或经营承诺。",
    ]
    return verified, unverified, cannot


def _metric_row(metric: Metric) -> str:
    display = metric_display(metric)
    status = (
        '<span class="status-badge status-good">可计算</span>'
        if metric.status == "computed"
        else '<span class="status-badge status-neutral">不可计算</span>'
    )
    note = f'<span class="cell-secondary">{esc(metric.note)}</span>' if metric.note else ""
    return (
        "<tr>"
        f"<td>{esc(metric.label)}{note}</td>"
        f'<td class="num">{esc(display["prior"])}</td>'
        f'<td class="num">{esc(display["value"])}</td>'
        f'<td>{esc(display["delta"])}</td>'
        f'<td class="formula">{esc(metric.formula)}</td>'
        f"<td>{status}</td>"
        "</tr>"
    )


def _finding_html(finding: Finding) -> str:
    risk_class = RISK_CLASSES.get(finding.risk_level, "risk-medium")
    status_class = STATUS_CLASSES.get(finding.status, "status-neutral")
    raw_rows = "".join(
        f"<tr><td>{esc(row.get('period', ''))}</td><td>{esc(row.get('item', ''))}</td><td>{esc(row.get('value', ''))}</td><td>{esc(row.get('source', ''))}</td></tr>"
        for row in finding.raw_data
    )
    evidence_links = " ".join(
        f'<a class="cite" href="#{esc(evidence_id)}">{esc(evidence_id)}</a>' for evidence_id in finding.evidence_ids
    )
    return f"""
    <div class="report-finding">
      <div class="report-finding-head">
        <span class="risk-pill {risk_class}">{esc(RISK_LABELS[finding.risk_level])}</span>
        <strong>{esc(finding.id)} · {esc(finding.title)}</strong>
        <span class="status-badge {status_class}">{esc(finding.status_label)}</span>
      </div>
      <p>{esc(finding.evidence_summary)} {evidence_links}</p>
      <p class="finding-limit">规则 {esc(finding.rule_id)}：{esc(finding.rule_condition)}</p>
      <p class="finding-limit">限制：{esc(finding.limitation)}</p>
      <details class="report-detail">
        <summary>查看原始数据与公式</summary>
        <table class="report-table">
          <thead><tr><th>期间</th><th>项目</th><th>原始值</th><th>来源</th></tr></thead>
          <tbody>{raw_rows}</tbody>
        </table>
        <div class="formula-box">{esc(finding.formula)}</div>
      </details>
    </div>"""


def _step_rows(state: AnalysisState) -> str:
    rows = []
    for step in state.steps:
        rows.append(
            "<tr>"
            f"<td>{esc(step.seq):0>2}</td>"
            f"<td>{esc(step.title)}<span class=\"cell-secondary\">{esc(step.purpose)}</span></td>"
            f"<td><span class=\"mono\">{esc(step.tool)}</span></td>"
            f"<td>{esc(step.status_label)}</td>"
            f"<td>{esc(step.result_summary)}</td>"
            f"<td class=\"num\">{esc(step.duration_ms if step.duration_ms is not None else '—')} ms</td>"
            "</tr>"
        )
    return "".join(rows)


def _scenario_html(scenario: ScenarioResult | None) -> str:
    if scenario is None:
        return '<p class="report-note">本次分析尚未运行情景模拟。</p>'
    b = scenario.baseline
    return f"""
    <div class="report-scenario-box">
      <div><span>基准净利润</span><strong>{fmt_wan(b.net_profit)}</strong></div>
      <div><span>模拟净利润</span><strong>{fmt_wan(scenario.net_profit)}</strong></div>
      <div><span>模拟净利率</span><strong>{fmt_pct(scenario.net_margin_pct)}</strong></div>
      <div><span>变化</span><strong>{fmt_pct(scenario.delta_pct, signed=True)}</strong></div>
    </div>
    <p class="report-note">参数：收入 {scenario.params.revenue_change_pct:+.1f}% · 成本率 {scenario.params.cost_rate_change_pp:+.1f}pp · 销售费用 {scenario.params.selling_expense_change_pct:+.1f}%。{esc(" ".join(scenario.limitations))}</p>
    """


def _source_rows(dataset: Dataset) -> str:
    rows = []
    for file in dataset.files:
        rows.append(
            f"<tr><td>{esc(file.name)}</td><td class=\"num\">{file.size_bytes:,}</td><td class=\"mono\">{esc(file.sha256[:16])}…</td></tr>"
        )
    return "".join(rows)


def _quality_rows(dataset: Dataset) -> str:
    rows = []
    for issue in dataset.quality[:12]:
        severity = {"blocker": "高风险 / 阻塞", "warning": "警告", "info": "提示"}.get(issue.severity, issue.severity)
        rows.append(
            f"<tr><td>{esc(issue.code)}</td><td>{esc(severity)}</td><td>{esc(issue.message)}</td><td>{esc(issue.impact or issue.detail)}</td></tr>"
        )
    return "".join(rows)


def _evidence_rows(evidence: list[Evidence]) -> str:
    rows = []
    for item in evidence:
        rows.append(
            f"<tr id=\"{esc(item.id)}\"><td class=\"mono\">{esc(item.id)}</td>"
            f"<td>{esc(item.source_file)}<span class=\"cell-secondary\">{esc(item.source_sheet)} · 第 {item.source_row or '—'} 行</span></td>"
            f"<td>{esc(item.raw_item)}</td><td>{esc(item.period)}</td><td class=\"num\">{esc(item.raw_value)}</td>"
            f"<td>{esc(item.note or '—')}</td></tr>"
        )
    return "".join(rows)


def build_report(
    dataset: Dataset,
    state: AnalysisState,
    context: AnalysisContext,
    findings: list[Finding],
    narrative: str,
    scenario: ScenarioResult | None,
) -> tuple[str, str, str]:
    verified, unverified, cannot = build_conclusions(context, findings)
    score = quality_score(dataset)
    metric_table = "".join(_metric_row(metric) for metric in context.metrics)
    finding_html = "".join(_finding_html(finding) for finding in findings[:8])
    pending_html = "".join(
        f"<li><strong>{esc(question.title)}</strong>：{esc(question.detail)} <span class=\"cell-secondary\">需要：{esc(question.missing)}</span></li>"
        for question in state.pending_questions
    )
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    data_badge = "DEMO DATA / 演示数据" if dataset.is_demo else "LOCAL UPLOAD / 本地导入数据"
    risk_counts = {
        "high": len([f for f in findings if f.risk_level == "high"]),
        "medium": len([f for f in findings if f.risk_level == "medium"]),
        "low": len([f for f in findings if f.risk_level == "low"]),
    }
    model_badge = (
        '<span class="status-badge status-good">远程模型叙述已校验</span>'
        if state.model_status == "remote_success"
        else '<span class="status-badge status-neutral">本地确定性叙述</span>'
    )
    cover = f"""
    <header class="report-cover">
      <div class="report-cover-top">
        <span class="report-brand">FinGuard</span>
        <span class="status-badge status-warning">草稿 · 待人工复核</span>
      </div>
      <p class="report-overline">FINANCIAL DIAGNOSTIC REPORT</p>
      <h2>利润背离与现金流质量诊断</h2>
      <p class="report-subtitle">{esc(dataset.company)} · {esc(state.period_label)}</p>
      <div class="report-cover-meta">
        <div><span>报告编号</span><strong>{esc(state.id)}</strong></div>
        <div><span>分析期间</span><strong>{esc(state.period_label)}</strong></div>
        <div><span>数据质量</span><strong>{score} / 100</strong></div>
        <div><span>生成模式</span><strong>{'远程模型 + 本地计算校验' if state.model_status == 'remote_success' else '本地确定性运行时'}</strong></div>
      </div>
      <p class="report-disclaimer-inline">数据来源：{esc(data_badge)} · {esc(dataset.source_label)}。本报告属于辅助分析草稿，不构成审计、鉴证、税务或投资建议。生成时间：{esc(generated_at)}。</p>
    </header>
    """

    body_html = f"""{cover}
    <section class="report-section" id="report-exec">
      <div class="report-section-head"><span>01</span><h3>执行摘要</h3></div>
      <p>{esc(narrative)}</p>
      <div class="report-callout"><strong>当前状态</strong><p>报告草稿 · 待人工复核。风险计数：高风险 {risk_counts['high']} 条、中风险 {risk_counts['medium']} 条、低风险/待补证 {risk_counts['low']} 条。所有异常均为核查信号，不是定性结论。</p></div>
      {model_badge}
    </section>

    <section class="report-section" id="report-quality">
      <div class="report-section-head"><span>02</span><h3>企业与数据范围</h3></div>
      <p>企业：{esc(dataset.company)}。分析期间：{esc(state.period_label)}；对比期间：{esc(state.prior_period or '未识别')}。数据来源：{esc(dataset.source_label or '本地导入数据')}。数据质量评分 {score}/100。</p>
      <table class="report-table">
        <thead><tr><th>文件</th><th>大小（字节）</th><th>SHA-256 摘要</th></tr></thead>
        <tbody>{_source_rows(dataset)}</tbody>
      </table>
      <h3 class="report-subhead">校验问题</h3>
      <table class="report-table">
        <thead><tr><th>规则</th><th>级别</th><th>问题</th><th>影响</th></tr></thead>
        <tbody>{_quality_rows(dataset)}</tbody>
      </table>
    </section>

    <section class="report-section" id="report-metrics">
      <div class="report-section-head"><span>03</span><h3>关键财务指标</h3></div>
      <p>金额按人民币元计算，界面以万元展示；比例按同一套指标函数计算。带“不可计算”的指标没有被补成 0。</p>
      <table class="report-table">
        <thead><tr><th>指标</th><th>上年同期</th><th>本期</th><th>变化</th><th>公式 / 口径</th><th>状态</th></tr></thead>
        <tbody>{metric_table}</tbody>
      </table>
      <p class="report-note">期间天数：{context.days or '—'} 天。应收账款周转天数按期末口径计算。</p>
    </section>

    <section class="report-section" id="report-analysis">
      <div class="report-section-head"><span>04</span><h3>利润变化与现金流质量</h3></div>
      <p>收入、成本与利润的方向由本期与上年同期报表计算。经营现金流与净利润的比值仅说明利润的现金含量，不直接等同于利润质量结论。营运资本占用由应收账款、其他应收款、存货和应付账款的期末变化计算；没有来源的变动不会被写入事实。</p>
      <ul class="report-limits-list">
        {''.join(f'<li>{esc(item)}</li>' for item in verified)}
      </ul>
      <div class="report-callout"><strong>解释边界</strong><p>{esc(' '.join(unverified))}</p></div>
    </section>

    <section class="report-section" id="report-findings">
      <div class="report-section-head"><span>05</span><h3>风险发现与触发规则</h3></div>
      {finding_html or '<p class="report-note">没有生成异常发现。</p>'}
    </section>

    <section class="report-section" id="report-evidence">
      <div class="report-section-head"><span>06</span><h3>证据与计算过程</h3></div>
      <p>每个证据 ID 指向原始文件、工作表、行号、期间与原始值。公式和工具输出来自同一分析运行。</p>
      <table class="report-table evidence-table">
        <thead><tr><th>证据</th><th>来源</th><th>项目</th><th>期间</th><th>原始值</th><th>备注</th></tr></thead>
        <tbody>{_evidence_rows(state.evidence[:40])}</tbody>
      </table>
    </section>

    <section class="report-section" id="report-scenario">
      <div class="report-section-head"><span>07</span><h3>情景模拟</h3></div>
      {_scenario_html(scenario)}
    </section>

    <section class="report-section" id="report-agent">
      <div class="report-section-head"><span>08</span><h3>Agent 实际执行过程</h3></div>
      <table class="report-table">
        <thead><tr><th>#</th><th>步骤</th><th>工具</th><th>状态</th><th>结果摘要</th><th>耗时</th></tr></thead>
        <tbody>{_step_rows(state)}</tbody>
      </table>
    </section>

    <section class="report-section" id="report-actions">
      <div class="report-section-head"><span>09</span><h3>待人工核验与后续建议</h3></div>
      <ul class="report-limits-list">{pending_html or '<li>当前没有单独列示的待核验问题。</li>'}</ul>
      <ol class="report-action-list">
        <li><strong>补齐连续期间数据。</strong><span>获取更早期间应收账款、OCF 与净利润，验证连续两期规则。</span></li>
        <li><strong>补齐明细。</strong><span>获取其他应收款对手方/性质、销售费用明细、存货平均余额与库龄。</span></li>
        <li><strong>核对截止性。</strong><span>抽取季末月大额收入，核对合同、发货、签收与期后退货。</span></li>
        <li><strong>复核情景模拟。</strong><span>情景结果只用于讨论假设，不作为预算、预测或投资建议。</span></li>
      </ol>
    </section>

    <section class="report-section report-limits" id="report-limits">
      <div class="report-section-head"><span>10</span><h3>分析限制</h3></div>
      <ul>
        <li>数据来自用户提供的报表；未取得总账、明细账、合同、发票、银行流水或业务系统数据。</li>
        <li>单期异常不能替代连续期间验证；候选风险不会被写成已证实结论。</li>
        <li>应收账款周转天数使用期末口径；存货平均余额缺失时该指标保持不可计算。</li>
        <li>{esc(" ".join(cannot))}</li>
        <li>远程模型仅用于定性叙述；所有数字由 Python 计算并在报告核验步骤中检查。{esc('已启用远程模型：' + state.model_status if state.model_status != 'local' else '当前未启用远程模型。')}</li>
      </ul>
      <p class="report-signoff">FinGuard · 辅助财务分析工具 · 非正式审计或鉴证意见</p>
    </section>
    """

    appendix_html = f"""
    <header class="report-cover report-cover-compact">
      <div class="report-cover-top"><span class="report-brand">FinGuard</span><span class="status-badge status-warning">证据附录 · 草稿</span></div>
      <p class="report-overline">EVIDENCE APPENDIX</p>
      <h2>证据与计算附录</h2>
      <p class="report-subtitle">{esc(dataset.company)} · {esc(state.period_label)} · {len(state.evidence)} 条可追溯证据</p>
    </header>
    <section class="report-section">
      <div class="report-section-head"><span>A</span><h3>证据索引</h3></div>
      <table class="report-table evidence-table">
        <thead><tr><th>证据 ID</th><th>来源</th><th>原始项目</th><th>期间</th><th>原始值</th><th>备注</th></tr></thead>
        <tbody>{_evidence_rows(state.evidence)}</tbody>
      </table>
    </section>
    <section class="report-section">
      <div class="report-section-head"><span>B</span><h3>公式与计算</h3></div>
      <table class="report-table">
        <thead><tr><th>指标</th><th>公式</th><th>本期结果</th><th>状态</th></tr></thead>
        <tbody>{''.join(f'<tr><td>{esc(m.label)}</td><td class="formula">{esc(m.formula)}</td><td>{esc(metric_display(m)["value"])}</td><td>{esc(m.status_label)}</td></tr>' for m in context.metrics)}</tbody>
      </table>
    </section>
    <section class="report-section">
      <div class="report-section-head"><span>C</span><h3>待补证清单</h3></div>
      <ul class="report-limits-list">{pending_html or '<li>无</li>'}</ul>
    </section>
    <p class="report-signoff">FinGuard · 证据附录 · 辅助分析草稿 · 非正式审计或鉴证意见</p>
    """

    markdown = _markdown_report(
        dataset=dataset,
        state=state,
        context=context,
        findings=findings,
        narrative=narrative,
        scenario=scenario,
        verified=verified,
        unverified=unverified,
        cannot=cannot,
    )
    return markdown, body_html, appendix_html


def _md_escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _markdown_report(
    *,
    dataset: Dataset,
    state: AnalysisState,
    context: AnalysisContext,
    findings: list[Finding],
    narrative: str,
    scenario: ScenarioResult | None,
    verified: list[str],
    unverified: list[str],
    cannot: list[str],
) -> str:
    metric_rows = "\n".join(
        f"| {_md_escape(m.label)} | {_md_escape(metric_display(m)['prior'])} | {_md_escape(metric_display(m)['value'])} | {_md_escape(metric_display(m)['delta'])} | {_md_escape(m.status_label)} |"
        for m in context.metrics
    )
    finding_blocks = []
    for finding in findings:
        finding_blocks.append(
            f"""### {finding.id} {finding.title}

- 风险：{RISK_LABELS[finding.risk_level]} / {finding.status_label}
- 规则：{finding.rule_id} · {finding.rule_condition}
- 指标：{finding.metric_value}（{finding.metric_sub}）
- 证据：{', '.join(finding.evidence_ids) or '无'}
- 摘要：{finding.evidence_summary}
- 公式：{finding.formula}
- 限制：{finding.limitation}
- 后续核查：{finding.recommendation}
"""
        )
    pending = "\n".join(
        f"- **{question.title}**：{question.detail}（需要：{question.missing}）" for question in state.pending_questions
    )
    source_lines = "\n".join(
        f"- `{file.name}` · {file.size_bytes:,} bytes · SHA-256 `{file.sha256[:16]}…`" for file in dataset.files
    )
    quality_lines = "\n".join(
        f"- [{issue.severity}] {issue.code} · {issue.message} · {issue.impact or issue.detail}"
        for issue in dataset.quality[:15]
    )
    metric_table = (
        "| 指标 | 上年同期 | 本期 | 变化 | 状态 |\n"
        "|---|---:|---:|---:|---|\n"
        f"{metric_rows}"
    )
    scenario_md = "本次分析尚未运行情景模拟。"
    if scenario:
        scenario_md = (
            f"- 参数：收入 {scenario.params.revenue_change_pct:+.1f}% · 成本率 {scenario.params.cost_rate_change_pp:+.1f}pp · 销售费用 {scenario.params.selling_expense_change_pct:+.1f}%\n"
            f"- 基准净利润：{fmt_wan(scenario.baseline.net_profit)}\n"
            f"- 模拟净利润：{fmt_wan(scenario.net_profit)}\n"
            f"- 模拟净利率：{fmt_pct(scenario.net_margin_pct)}\n"
            f"- 变化：{fmt_pct(scenario.delta_pct, signed=True)}\n"
            f"- 限制：{' '.join(scenario.limitations)}"
        )
    step_lines = "\n".join(
        f"- {step.seq:02d} **{step.title}** · `{step.tool}` · {step.status_label} · {step.result_summary}"
        for step in state.steps
    )
    evidence_lines = "\n".join(
        f"- `{item.id}` · {item.source_file}（{item.source_sheet} 第 {item.source_row or '—'} 行） · {item.period} · {item.raw_item} = {item.raw_value}"
        for item in state.evidence
    )
    return f"""# FinGuard 财务诊断报告

> **{'DEMO DATA / 演示数据' if dataset.is_demo else 'LOCAL UPLOAD / 本地导入数据'} · 辅助分析草稿**  
> 企业：{dataset.company}  
> 期间：{state.period_label}  
> 对比：{state.prior_period or '未识别'}  
> 生成模式：{'远程模型叙述 + 本地计算' if state.model_status == 'remote_success' else '本地确定性运行时'}  
> 状态：草稿 · 待人工复核  
> 报告编号：{state.id}

## 01 执行摘要

{narrative}

已验证事实：

{chr(10).join(f'- {item}' for item in verified)}

待核验：

{chr(10).join(f'- {item}' for item in unverified)}

不能得出的结论：

{chr(10).join(f'- {item}' for item in cannot)}

## 02 企业与数据范围

- 企业：{dataset.company}
- 来源：{dataset.source_label}
- 数据质量评分：{quality_score(dataset)}/100
- 期间：{state.period_label}
- 文件：

{source_lines}

### 校验问题

{quality_lines}

## 03 关键财务指标

{metric_table}

金额按人民币元计算，界面以万元展示；“不可计算”不补 0。

## 04 利润变化与经营现金流质量

{chr(10).join(f'- {item}' for item in verified)}

解释边界：见第 06 节待核验事项。

## 05 风险发现

{chr(10).join(finding_blocks)}

## 06 证据与计算过程

{evidence_lines}

## 07 情景模拟

{scenario_md}

## 08 Agent 实际执行过程

{step_lines}

## 09 待人工核验

{pending or '- 无'}

## 10 分析限制

{chr(10).join(f'- {item}' for item in cannot)}
- 未取得总账、明细账、合同、发票、银行流水或业务系统数据。
- 单期异常不替代连续期间验证；候选风险不等同于已证实结论。
- 应收账款周转天数使用期末口径；存货平均余额缺失时该指标保持不可计算。
- 远程模型仅用于定性叙述，所有数字由程序计算并核验；当前模型状态：{state.model_status}。

---
FinGuard · 辅助财务分析工具 · 非正式审计或鉴证意见
"""
