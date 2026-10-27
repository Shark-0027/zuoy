from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

StatementType = Literal["income", "balance", "cashflow", "expense_detail", "unknown"]
FactStatus = Literal["valid", "missing", "invalid", "unmapped", "duplicate"]
QualitySeverity = Literal["blocker", "warning", "info"]
RiskLevel = Literal["high", "medium", "low"]
FindingStatus = Literal["triggered", "candidate", "not_triggered", "insufficient_data", "needs_evidence"]
AnalysisStatus = Literal[
    "created",
    "running",
    "paused",
    "awaiting_review",
    "completed",
    "failed",
    "cancelled",
    "blocked",
]
StepStatus = Literal["pending", "running", "completed", "failed", "waiting_review", "skipped"]


def utcnow() -> datetime:
    return datetime.now(UTC)


class FileMeta(BaseModel):
    name: str
    stored_name: str
    size_bytes: int
    sha256: str
    content_type: str = ""


class PeriodInfo(BaseModel):
    raw: str
    start: date | None = None
    end: date | None = None
    kind: Literal["ytd", "quarter", "month", "instant", "unknown"] = "unknown"
    label: str = ""
    days: int | None = None


class Fact(BaseModel):
    id: str
    statement: StatementType = "unknown"
    field: str | None = None
    raw_item: str
    period_raw: str
    period_start: date | None = None
    period_end: date | None = None
    period_kind: str = "unknown"
    period_label: str = ""
    value: Decimal | None = None
    raw_value: str = ""
    unit_raw: str = ""
    unit: str = "CNY"
    source_file: str
    source_sheet: str = ""
    source_row: int
    status: FactStatus = "valid"
    note: str = ""


class QualityIssue(BaseModel):
    id: str
    code: str
    severity: QualitySeverity
    category: str
    message: str
    detail: str = ""
    impact: str = ""
    field: str | None = None
    period: str | None = None
    source_file: str | None = None
    source_row: int | None = None
    blocking: bool = False


class MappingColumn(BaseModel):
    source: str
    target: str | None = None
    confidence: float = 0.0
    sample: str = ""
    status: str = "mapped"


class MappingItem(BaseModel):
    raw_item: str
    field: str | None = None
    canonical_label: str | None = None
    confidence: float = 0.0
    status: str = "mapped"
    source_file: str
    source_row: int
    sample_value: str = ""
    note: str = ""


class MappingInfo(BaseModel):
    layout: str = "long"
    columns: list[MappingColumn] = Field(default_factory=list)
    items: list[MappingItem] = Field(default_factory=list)
    item_column: str | None = None
    period_columns: list[str] = Field(default_factory=list)
    statement_hint: str = ""
    unit_hint: str = ""


class RawPreview(BaseModel):
    file: str
    sheet: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    total_rows: int = 0


class Dataset(BaseModel):
    id: str
    company: str
    name: str
    is_demo: bool = False
    files: list[FileMeta] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    quality: list[QualityIssue] = Field(default_factory=list)
    mapping: MappingInfo = Field(default_factory=MappingInfo)
    previews: list[RawPreview] = Field(default_factory=list)
    periods: list[PeriodInfo] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    source_label: str = ""

    @property
    def blocking_issues(self) -> list[QualityIssue]:
        return [issue for issue in self.quality if issue.blocking]

    @property
    def warning_issues(self) -> list[QualityIssue]:
        return [issue for issue in self.quality if issue.severity == "warning" and not issue.blocking]


class Metric(BaseModel):
    id: str
    label: str
    value: Decimal | None = None
    prior: Decimal | None = None
    delta: Decimal | None = None
    unit: str = "CNY"
    change_type: str = "absolute"
    formula: str = ""
    status: Literal["computed", "not_computable"] = "computed"
    status_label: str = "可计算"
    period: str = ""
    prior_period: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    limitation: str = ""
    note: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    id: str
    label: str
    source_file: str
    source_sheet: str = ""
    source_row: int | None = None
    raw_item: str
    period: str
    raw_value: str = ""
    normalized_value: str = ""
    formula: str = ""
    result: str = ""
    note: str = ""
    evidence_type: str = "fact"
    fact_id: str | None = None


class Finding(BaseModel):
    id: str
    title: str
    risk_level: RiskLevel = "medium"
    status: FindingStatus = "candidate"
    status_label: str = "待核验"
    rule_id: str
    rule_name: str
    rule_condition: str
    period: str
    metric_label: str
    metric_value: str
    metric_sub: str = ""
    evidence_summary: str
    recommendation: str
    limitation: str
    raw_data: list[dict[str, str]] = Field(default_factory=list)
    formula: str = ""
    basis: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class PendingQuestion(BaseModel):
    id: str
    title: str
    detail: str
    severity: str = "warning"
    missing: str = ""
    action: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class AgentStep(BaseModel):
    id: str
    seq: int
    title: str
    purpose: str
    tool: str
    status: StepStatus = "pending"
    status_label: str = "待执行"
    input_params: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    explanation: str = ""


class ScenarioParams(BaseModel):
    revenue_change_pct: float = Field(default=4.0, ge=-50, le=100)
    cost_rate_change_pp: float = Field(default=-1.0, ge=-30, le=30)
    selling_expense_change_pct: float = Field(default=6.0, ge=-80, le=200)


class ScenarioBaseline(BaseModel):
    revenue: float
    gross_margin_pct: float
    selling_expenses: float
    admin_expenses: float
    rd_expenses: float
    finance_expenses: float
    other_income_net: float
    income_tax: float
    tax_rate_pct: float
    net_profit: float
    net_margin_pct: float
    source_fields: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ScenarioResult(BaseModel):
    params: ScenarioParams
    baseline: ScenarioBaseline
    revenue: float
    gross_margin_pct: float
    gross_profit: float
    selling_expenses: float
    pretax_profit: float
    net_profit: float
    net_margin_pct: float
    delta_amount: float
    delta_pct: float
    formula: str
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    is_simulation: bool = True


class ReviewDecision(BaseModel):
    decision: Literal["confirm_limitations", "cancel"] = "confirm_limitations"
    note: str = ""


class LLMInsight(BaseModel):
    analysis_id: str
    status: Literal["remote_success", "remote_failed_fallback", "remote_rejected_fallback", "local"]
    status_label: str = "远程模型解读"
    model: str = ""
    generated_at: datetime = Field(default_factory=utcnow)
    summary: str = ""
    signals: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    error: str = ""
    data_scope: str = ""
    available: bool = False


class AnalysisState(BaseModel):
    id: str
    dataset_id: str
    company: str
    goal: str
    period_label: str = ""
    current_period: str = ""
    prior_period: str = ""
    status: AnalysisStatus = "created"
    status_label: str = "待执行"
    plan_summary: str = ""
    steps: list[AgentStep] = Field(default_factory=list)
    current_step_index: int = 0
    metrics: list[Metric] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    pending_questions: list[PendingQuestion] = Field(default_factory=list)
    pause_reason: str = ""
    review: dict[str, Any] = Field(default_factory=dict)
    narrative: str = ""
    model_status: str = "local"
    model_error: str = ""
    scenario: ScenarioResult | None = None
    llm_insight: LLMInsight | None = None
    report_markdown: str = ""
    report_html: str = ""
    report_appendix_html: str = ""
    verified_conclusions: list[str] = Field(default_factory=list)
    unverified_conclusions: list[str] = Field(default_factory=list)
    cannot_conclude: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    error: str = ""

    @property
    def progress_done(self) -> int:
        return len([s for s in self.steps if s.status in {"completed", "skipped"}])

    @property
    def progress_total(self) -> int:
        return len(self.steps)
