from __future__ import annotations

import time
import uuid
from datetime import datetime

from finguard.analysis.formatting import metric_display
from finguard.analysis.metrics import AnalysisContext, compute_metrics
from finguard.analysis.report import build_conclusions, build_report
from finguard.analysis.rules import detect_findings
from finguard.analysis.scenario import ScenarioUnavailable, run_scenario
from finguard.analysis.tools import (
    build_evidence,
    build_pending_questions,
    cash_quality_analysis,
    revenue_profit_analysis,
    verify_report_text,
)
from finguard.llm import LLM_DATA_SCOPE, NarrativeClient
from finguard.models import (
    AgentStep,
    AnalysisState,
    Dataset,
    Finding,
    LLMInsight,
    ReviewDecision,
    ScenarioParams,
    ScenarioResult,
    utcnow,
)

STEP_SPECS: list[dict[str, str]] = [
    {
        "id": "validate_data",
        "title": "校验数据与字段",
        "purpose": "确认期间、单位、字段和关键数据质量是否足以支撑分析。",
        "tool": "validate_dataset",
        "explanation": "先检查数据能不能用，避免在缺失字段上生成看似精确的数字。",
    },
    {
        "id": "compute_metrics",
        "title": "计算财务指标",
        "purpose": "用确定性 Python 工具计算同比、利润率、周转和现金流指标。",
        "tool": "compute_metrics",
        "explanation": "数字由程序计算；模型不参与关键算术。",
    },
    {
        "id": "revenue_profit",
        "title": "分析收入与利润变化",
        "purpose": "解释收入、成本、毛利率与净利润的变化方向。",
        "tool": "analyze_revenue_profit",
        "explanation": "先确认收入增长与利润变化是否同步，再讨论原因。",
    },
    {
        "id": "cash_quality",
        "title": "分析经营现金流质量",
        "purpose": "比较 OCF 与净利润，拆解营运资本占用。",
        "tool": "analyze_cash_quality",
        "explanation": "利润是会计结果，现金流是另一条证据链。",
    },
    {
        "id": "anomalies",
        "title": "检查风险规则",
        "purpose": "执行版本化规则，生成风险候选与证据引用。",
        "tool": "detect_anomalies",
        "explanation": "规则触发只是核查信号；连续期间与证据不足会降级。",
    },
    {
        "id": "evidence",
        "title": "核验证据并判断是否暂停",
        "purpose": "检查结论是否拥有原始数据、公式和足够背景资料。",
        "tool": "verify_evidence",
        "explanation": "证据不足时，Agent 必须停下来，而不是把推断写成事实。",
    },
    {
        "id": "report",
        "title": "生成结构化报告",
        "purpose": "将指标、规则、证据、限制和 Agent 执行记录组织成草稿。",
        "tool": "generate_report",
        "explanation": "报告由同一分析状态生成，页面与导出文件共享数字。",
    },
    {
        "id": "verify_report",
        "title": "核验报告一致性",
        "purpose": "检查章节、数字、引用、禁用措辞和限制说明。",
        "tool": "verify_report",
        "explanation": "报告生成后再做一次程序核验，避免数字或结论失去依据。",
    },
]


class AgentStateError(RuntimeError):
    pass


STATUS_LABELS = {
    "created": "待执行",
    "running": "执行中",
    "paused": "等待人工核验",
    "awaiting_review": "等待人工核验",
    "completed": "已完成 · 待人工复核",
    "failed": "执行失败",
    "cancelled": "已取消",
    "blocked": "数据不足 · 需补充",
}

STEP_STATUS_LABELS = {
    "pending": "待执行",
    "running": "执行中",
    "completed": "已完成",
    "failed": "失败",
    "waiting_review": "等待人工核验",
    "skipped": "已跳过",
}


class AgentRuntime:
    def __init__(self, narrative_client: NarrativeClient):
        self.narrative_client = narrative_client

    def create_analysis(self, dataset: Dataset, goal: str | None = None) -> AnalysisState:
        try:
            context = compute_metrics(dataset)
            period_label = context.period_label
            prior_label = context.prior_period_label
        except Exception:
            period_label = "期间未识别"
            prior_label = "上年同期未识别"
        analysis_id = f"AN-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        steps = [
            AgentStep(
                id=spec["id"],
                seq=index + 1,
                title=spec["title"],
                purpose=spec["purpose"],
                tool=spec["tool"],
                status="pending",
                status_label=STEP_STATUS_LABELS["pending"],
                input_params=self._input_params_for(spec["id"], dataset, analysis_id),
                explanation=spec["explanation"],
            )
            for index, spec in enumerate(STEP_SPECS)
        ]
        state = AnalysisState(
            id=analysis_id,
            dataset_id=dataset.id,
            company=dataset.company,
            goal=goal
            or "评估本期收入增长与利润、现金流变化之间的关系，识别可核查的异常，并说明证据边界。",
            period_label=period_label,
            current_period=period_label,
            prior_period=prior_label,
            status="created",
            status_label=STATUS_LABELS["created"],
            plan_summary=(
                f"基于 {len(dataset.files)} 个来源文件，执行数据校验、指标计算、收入利润分析、"
                "现金流质量分析、规则检查、证据核验与报告生成。"
            ),
            steps=steps,
        )
        return state

    def _input_params_for(self, step_id: str, dataset: Dataset, analysis_id: str) -> dict[str, object]:
        if step_id == "validate_data":
            return {"dataset_id": dataset.id, "source_files": [file.name for file in dataset.files]}
        if step_id == "compute_metrics":
            return {"dataset_id": dataset.id, "comparison": "latest_ytd_vs_prior_year"}
        if step_id == "revenue_profit":
            return {"dataset_id": dataset.id, "metrics": ["revenue", "cogs", "gross_margin", "net_profit"]}
        if step_id == "cash_quality":
            return {"dataset_id": dataset.id, "metrics": ["operating_cash_flow", "ocf_to_net_profit", "working_capital_drag"]}
        if step_id == "anomalies":
            return {"dataset_id": dataset.id, "ruleset": "FinGuard rules v0.2"}
        if step_id == "evidence":
            return {"analysis_id": analysis_id, "finding_evidence": "required"}
        if step_id == "report":
            return {
                "analysis_id": analysis_id,
                "format": "markdown+html",
                "model_narrative": self.narrative_client.configured,
            }
        if step_id == "verify_report":
            return {"analysis_id": analysis_id, "checks": ["sections", "numbers", "citations", "banned_phrases"]}
        return {}

    def advance(self, state: AnalysisState, dataset: Dataset) -> AnalysisState:
        if state.status in {"paused", "awaiting_review"}:
            raise AgentStateError("分析正在等待人工核验，请先确认限制或取消。")
        if state.status in {"blocked", "failed", "cancelled"}:
            raise AgentStateError("当前分析已停止，请重新执行或补充数据。")
        if state.status == "completed":
            return state
        try:
            index = next(i for i, step in enumerate(state.steps) if step.status == "pending")
        except StopIteration:
            state.status = "completed"
            state.status_label = STATUS_LABELS["completed"]
            state.updated_at = utcnow()
            return state

        step = state.steps[index]
        state.current_step_index = index
        state.status = "running"
        state.status_label = STATUS_LABELS["running"]
        step.status = "running"
        step.status_label = STEP_STATUS_LABELS["running"]
        step.started_at = utcnow()
        step.error = None
        started = time.perf_counter()
        try:
            outcome = self._execute_step(step, state, dataset)
            step.result = outcome.get("result", {})
            step.result_summary = str(outcome.get("summary", ""))
            step.evidence_ids = list(outcome.get("evidence_ids", []))
            step.finished_at = utcnow()
            step.duration_ms = int((time.perf_counter() - started) * 1000)
            if outcome.get("pause"):
                step.status = "waiting_review"
                step.status_label = STEP_STATUS_LABELS["waiting_review"]
                state.status = "paused" if outcome.get("pause_kind") != "blocked" else "blocked"
                state.status_label = (
                    STATUS_LABELS["paused"] if outcome.get("pause_kind") != "blocked" else STATUS_LABELS["blocked"]
                )
                state.pause_reason = str(outcome.get("pause_reason", ""))
                state.pending_questions = outcome.get("questions", [])
                state.current_step_index = index
            else:
                step.status = "completed"
                step.status_label = STEP_STATUS_LABELS["completed"]
                state.current_step_index = index + 1
                if index + 1 >= len(state.steps):
                    state.status = "completed"
                    state.status_label = STATUS_LABELS["completed"]
                else:
                    state.status = "running"
                    state.status_label = STATUS_LABELS["running"]
            if step.id == "verify_report" and state.status == "completed":
                self._refresh_report(state, dataset)
            state.updated_at = utcnow()
            return state
        except Exception as exc:  # noqa: BLE001
            step.status = "failed"
            step.status_label = STEP_STATUS_LABELS["failed"]
            step.error = str(exc)[:500]
            step.finished_at = utcnow()
            step.duration_ms = int((time.perf_counter() - started) * 1000)
            state.status = "failed"
            state.status_label = STATUS_LABELS["failed"]
            state.error = str(exc)[:500]
            state.updated_at = utcnow()
            return state

    def _execute_step(self, step: AgentStep, state: AnalysisState, dataset: Dataset) -> dict[str, object]:
        if step.id == "validate_data":
            blockers = [issue for issue in dataset.quality if issue.blocking]
            if blockers:
                try:
                    context_for_questions = compute_metrics(dataset)
                except Exception:
                    context_for_questions = None
                questions = (
                    build_pending_questions(dataset, context_for_questions, [])
                    if context_for_questions is not None
                    else []
                )
                step.explanation = "数据存在阻塞问题，Agent 不能继续在不可靠的数据上计算。"
                return {
                    "summary": f"发现 {len(blockers)} 个阻塞问题：{blockers[0].message}",
                    "result": {
                        "blockers": [issue.model_dump(mode="json") for issue in blockers],
                        "quality_issue_count": len(dataset.quality),
                    },
                    "pause": True,
                    "pause_kind": "blocked",
                    "pause_reason": "；".join(issue.message for issue in blockers[:3]),
                    "questions": questions,
                }
            warnings = len([issue for issue in dataset.quality if issue.severity == "warning"])
            return {
                "summary": f"数据校验通过：{len(dataset.files)} 个文件、{len(dataset.facts)} 条原始记录、{warnings} 个警告。",
                "result": {
                    "files": [file.name for file in dataset.files],
                    "facts": len(dataset.facts),
                    "warnings": warnings,
                    "blockers": 0,
                    "quality_score": max(40, 100 - warnings * 3),
                },
            }

        context = compute_metrics(dataset)
        if step.id == "compute_metrics":
            state.metrics = context.metrics
            state.evidence = build_evidence(dataset)
            state.period_label = context.period_label
            state.current_period = context.period_label
            state.prior_period = context.prior_period_label
            computed = len([metric for metric in context.metrics if metric.status == "computed"])
            unavailable = len(context.metrics) - computed
            return {
                "summary": f"完成 {len(context.metrics)} 项指标：{computed} 项可计算、{unavailable} 项不可计算；期间 {context.period_label}。",
                "result": {
                    "metrics": [metric.model_dump(mode="json") for metric in context.metrics],
                    "period": context.period_label,
                    "prior_period": context.prior_period_label,
                    "days": context.days,
                    "evidence_count": len(state.evidence),
                },
                "evidence_ids": [evidence.id for evidence in state.evidence[:12]],
            }

        if step.id == "revenue_profit":
            data = revenue_profit_analysis(context)
            return {
                "summary": str(data["summary"]),
                "result": data,
                "evidence_ids": self._metric_evidence(context, ("revenue", "cogs", "gross_margin", "net_profit")),
            }

        if step.id == "cash_quality":
            data = cash_quality_analysis(context)
            return {
                "summary": str(data["summary"]),
                "result": data,
                "evidence_ids": self._metric_evidence(context, ("operating_cash_flow", "ocf_to_net_profit", "working_capital_drag")),
            }

        if step.id == "anomalies":
            findings = detect_findings(context)
            state.findings = findings
            high = len([finding for finding in findings if finding.risk_level == "high"])
            medium = len([finding for finding in findings if finding.risk_level == "medium"])
            low = len([finding for finding in findings if finding.risk_level == "low"])
            return {
                "summary": f"命中 {len(findings)} 条规则结果：高风险 {high}、中风险 {medium}、低风险/待补证 {low}。",
                "result": {
                    "findings": [finding.model_dump(mode="json") for finding in findings],
                    "counts": {"high": high, "medium": medium, "low": low},
                },
                "evidence_ids": [evidence_id for finding in findings for evidence_id in finding.evidence_ids],
            }

        if step.id == "evidence":
            findings = state.findings or detect_findings(context)
            state.findings = findings
            state.evidence = state.evidence or build_evidence(dataset)
            questions = build_pending_questions(dataset, context, findings)
            if questions:
                step.explanation = "证据核验发现连续期间、明细或截止性资料不足，Agent 在生成报告前暂停。"
                return {
                    "summary": f"证据核验暂停：{len(questions)} 项需要人工确认，报告尚未生成。",
                    "result": {
                        "questions": [question.model_dump(mode="json") for question in questions],
                        "evidence_count": len(state.evidence),
                    },
                    "evidence_ids": [evidence_id for finding in findings for evidence_id in finding.evidence_ids],
                    "pause": True,
                    "pause_kind": "review",
                    "pause_reason": "；".join(question.title for question in questions[:3]),
                    "questions": questions,
                }
            return {
                "summary": "证据充分，未发现需要暂停的人工核验项。",
                "result": {"questions": [], "evidence_count": len(state.evidence)},
                "evidence_ids": [],
            }

        if step.id == "report":
            findings = state.findings or detect_findings(context)
            state.findings = findings
            state.evidence = state.evidence or build_evidence(dataset)
            if state.scenario is None:
                try:
                    state.scenario = run_scenario(context, ScenarioParams())
                except ScenarioUnavailable:
                    state.scenario = None
            narrative_input = self._narrative_input(context, findings, state.pending_questions, qualitative=True)
            narrative_outcome = self.narrative_client.generate(**narrative_input)
            state.narrative = narrative_outcome.text
            state.model_status = narrative_outcome.status
            state.model_error = narrative_outcome.error
            verified, unverified, cannot = build_conclusions(context, findings)
            state.verified_conclusions = verified
            state.unverified_conclusions = unverified
            state.cannot_conclude = cannot
            markdown, html, appendix = build_report(
                dataset,
                state,
                context,
                findings,
                state.narrative,
                state.scenario,
            )
            state.report_markdown = markdown
            state.report_html = html
            state.report_appendix_html = appendix
            mode = "远程模型叙述已校验" if state.model_status == "remote_success" else "本地确定性叙述"
            return {
                "summary": f"生成报告草稿（{len(markdown):,} 字）· {mode}。",
                "result": {
                    "report_chars": len(markdown),
                    "model_status": state.model_status,
                    "model_error": state.model_error,
                    "scenario_included": state.scenario is not None,
                },
                "evidence_ids": [evidence.id for evidence in state.evidence[:12]],
            }

        if step.id == "verify_report":
            findings = state.findings or detect_findings(context)
            errors = verify_report_text(state.report_markdown, state, findings, context)
            if errors:
                raise AgentStateError("报告核验失败：" + "；".join(errors[:5]))
            return {
                "summary": "报告核验通过：章节、关键数字、发现引用和分析限制均存在。",
                "result": {
                    "checks": ["章节完整", "关键数字一致", "引用存在", "无未证实定性短语"],
                    "errors": [],
                },
            }

        raise AgentStateError(f"未知步骤：{step.id}")

    @staticmethod
    def _metric_evidence(context: AnalysisContext, metric_ids: tuple[str, ...]) -> list[str]:
        evidence_ids: list[str] = []
        for metric_id in metric_ids:
            metric = context.metric(metric_id)
            if not metric:
                continue
            for evidence_id in metric.evidence_ids:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        return evidence_ids

    @staticmethod
    def _narrative_input(
        context: AnalysisContext,
        findings: list[Finding],
        pending: list,
        *,
        qualitative: bool = False,
    ) -> dict[str, object]:
        if qualitative:
            metric_items = []
            for metric in context.metrics:
                if metric.delta is None:
                    direction = "unknown"
                elif metric.delta > 0:
                    direction = "up"
                elif metric.delta < 0:
                    direction = "down"
                else:
                    direction = "flat"
                metric_items.append(
                    {
                        "label": metric.label,
                        "direction": direction,
                        "status": metric.status_label,
                    }
                )
        else:
            metric_items = [
                {
                    "label": metric.label,
                    "value": metric_display(metric)["value"],
                    "delta": metric_display(metric)["delta"],
                    "status": metric.status_label,
                }
                for metric in context.metrics
            ]
        finding_summaries = [
            {
                "id": finding.id,
                "title": finding.title,
                "risk_level": finding.risk_level,
                "status": finding.status,
                "limitation": finding.limitation,
            }
            for finding in findings
        ]
        pending_summaries = [
            {
                "title": question.title,
                "detail": question.detail,
                "missing": question.missing,
            }
            for question in pending
        ]
        return {
            "metrics": metric_items,
            "findings": finding_summaries,
            "pending": pending_summaries,
        }

    def run_to_pause(self, state: AnalysisState, dataset: Dataset, max_steps: int = 12) -> AnalysisState:
        for _ in range(max_steps):
            if state.status in {"paused", "blocked", "completed", "failed", "cancelled"}:
                break
            state = self.advance(state, dataset)
            if state.status in {"paused", "blocked", "completed", "failed", "cancelled"}:
                break
        return state

    def review(self, state: AnalysisState, dataset: Dataset, decision: ReviewDecision) -> AnalysisState:
        if state.status not in {"paused", "blocked"}:
            raise AgentStateError("当前分析不在等待人工核验状态。")
        if state.status == "blocked":
            if decision.decision == "cancel":
                state.status = "cancelled"
                state.status_label = STATUS_LABELS["cancelled"]
                state.updated_at = utcnow()
                return state
            raise AgentStateError("数据存在阻塞问题，不能仅通过确认限制继续；请补充或修正源数据。")
        step = state.steps[state.current_step_index] if state.current_step_index < len(state.steps) else None
        if step is None or step.status != "waiting_review":
            raise AgentStateError("未找到等待核验的步骤。")
        if decision.decision == "cancel":
            step.status = "skipped"
            step.status_label = STEP_STATUS_LABELS["skipped"]
            state.status = "cancelled"
            state.status_label = STATUS_LABELS["cancelled"]
            state.updated_at = utcnow()
            return state
        step.status = "completed"
        step.status_label = "已完成（已确认限制）"
        step.finished_at = utcnow()
        step.result_summary = (
            str(step.result_summary)
            + " 用户已确认证据边界，允许生成带限制的报告草稿。"
            + (f" 备注：{decision.note}" if decision.note else "")
        )
        state.review = {
            "decision": decision.decision,
            "note": decision.note,
            "confirmed_at": utcnow().isoformat(),
            "acknowledged_questions": [question.id for question in state.pending_questions],
        }
        state.status = "running"
        state.status_label = STATUS_LABELS["running"]
        state.pause_reason = ""
        state.current_step_index = state.current_step_index + 1
        state.updated_at = utcnow()
        return state

    def _refresh_report(self, state: AnalysisState, dataset: Dataset) -> None:
        """Regenerate the report after final execution status changes.

        The report includes the Agent execution log, so it must be rebuilt after
        the final verification step rather than showing a stale "running" step.
        """
        try:
            context = compute_metrics(dataset)
            findings = state.findings or detect_findings(context)
            markdown, html, appendix = build_report(
                dataset,
                state,
                context,
                findings,
                state.narrative,
                state.scenario,
            )
            state.report_markdown = markdown
            state.report_html = html
            state.report_appendix_html = appendix
        except Exception as exc:  # noqa: BLE001
            state.error = f"报告刷新失败：{str(exc)[:300]}"

    def reset(self, state: AnalysisState, dataset: Dataset, goal: str | None = None) -> AnalysisState:
        new_state = self.create_analysis(dataset, goal or state.goal)
        new_state.id = state.id
        new_state.created_at = state.created_at
        return new_state

    def generate_llm_insight(self, state: AnalysisState, dataset: Dataset) -> LLMInsight:
        """Run an on-demand, validated LLM interpretation.

        This is separate from the deterministic Agent steps. It never changes
        execution status and never supplies financial numbers.
        """
        if not self.narrative_client.configured:
            raise AgentStateError("未配置远程模型；请先在 .env 中配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL。")
        if not state.metrics:
            raise AgentStateError("请先运行指标计算，再生成 AI 解读。")

        context = compute_metrics(dataset)
        findings = state.findings or detect_findings(context)
        state.findings = findings
        state.evidence = state.evidence or build_evidence(dataset)
        outcome = self.narrative_client.generate_insight(
            **self._narrative_input(context, findings, state.pending_questions, qualitative=True)
        )
        if outcome.status == "remote_success" and outcome.payload:
            insight = LLMInsight(
                analysis_id=state.id,
                status=outcome.status,
                status_label="远程模型解读已校验",
                model=self.narrative_client.settings.llm_model,
                summary=outcome.payload["summary"],
                signals=outcome.payload["signals"],
                questions=outcome.payload["questions"],
                limitations=outcome.payload["limitations"],
                error="",
                data_scope=LLM_DATA_SCOPE,
                available=True,
            )
        else:
            insight = LLMInsight(
                analysis_id=state.id,
                status=outcome.status,
                status_label="远程模型不可用 · 本地回退",
                model=self.narrative_client.settings.llm_model,
                error=outcome.error,
                data_scope=LLM_DATA_SCOPE,
                available=False,
            )
        state.llm_insight = insight
        state.updated_at = utcnow()
        return insight

    def apply_scenario(self, state: AnalysisState, dataset: Dataset, params: ScenarioParams) -> ScenarioResult:
        context = compute_metrics(dataset)
        result = run_scenario(context, params)
        state.scenario = result
        if state.report_markdown and state.status == "completed":
            findings = state.findings or detect_findings(context)
            markdown, html, appendix = build_report(
                dataset,
                state,
                context,
                findings,
                state.narrative,
                result,
            )
            state.report_markdown = markdown
            state.report_html = html
            state.report_appendix_html = appendix
        state.updated_at = utcnow()
        return result
