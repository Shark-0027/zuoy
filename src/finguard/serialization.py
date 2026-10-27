from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from finguard.analysis.formatting import metric_display
from finguard.analysis.metrics import compute_metrics
from finguard.models import AnalysisState, Dataset, Metric


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump())
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


def metric_payload(metric: Metric) -> dict[str, Any]:
    display = metric_display(metric)
    payload = to_jsonable(metric)
    payload["display"] = display
    return payload


def dataset_payload(dataset: Dataset, *, include_facts: bool = False) -> dict[str, Any]:
    payload = to_jsonable(dataset)
    payload.pop("facts", None)
    if include_facts:
        payload["facts"] = to_jsonable(dataset.facts)
    payload["facts_count"] = len(dataset.facts)
    payload["valid_facts"] = len([fact for fact in dataset.facts if fact.status == "valid"])
    payload["quality_score"] = max(
        40,
        100
        - 20 * len([issue for issue in dataset.quality if issue.blocking])
        - 3 * len([issue for issue in dataset.quality if issue.severity == "warning" and not issue.blocking]),
    )
    payload["blocker_count"] = len([issue for issue in dataset.quality if issue.blocking])
    payload["warning_count"] = len(
        [issue for issue in dataset.quality if issue.severity == "warning" and not issue.blocking]
    )
    return payload


def _risk_counts(state: AnalysisState) -> dict[str, int]:
    return {
        "high": len([finding for finding in state.findings if finding.risk_level == "high"]),
        "medium": len([finding for finding in state.findings if finding.risk_level == "medium"]),
        "low": len([finding for finding in state.findings if finding.risk_level == "low"]),
        "total": len(state.findings),
    }


def _charts(dataset: Dataset, state: AnalysisState) -> dict[str, Any]:
    try:
        context = compute_metrics(dataset)
    except Exception:
        return {"revenue_cost": [], "margin": [], "monthly_revenue": [], "q3_share": []}
    revenue = context.metric("revenue")
    cogs = context.metric("cogs")
    margin = context.metric("gross_margin")
    revenue_cost = []
    margin_data = []
    if revenue and cogs:
        if revenue.prior is not None and cogs.prior is not None:
            revenue_cost.append(
                {
                    "label": context.prior_period_label,
                    "revenue": float(revenue.prior),
                    "cost": float(cogs.prior),
                }
            )
        if revenue.value is not None and cogs.value is not None:
            revenue_cost.append(
                {
                    "label": context.period_label,
                    "revenue": float(revenue.value),
                    "cost": float(cogs.value),
                }
            )
    if margin:
        if margin.prior is not None:
            margin_data.append({"label": context.prior_period_label, "value": float(margin.prior)})
        if margin.value is not None:
            margin_data.append({"label": context.period_label, "value": float(margin.value)})
    monthly = []
    for facts in (
        context.monthly.get("prior_month_facts", []),
        context.monthly.get("current_month_facts", []),
    ):
        for fact in facts:
            if fact is not None and fact.value is not None:
                monthly.append(
                    {
                        "label": fact.period_label or fact.period_raw,
                        "value": float(fact.value),
                        "period": fact.period_raw,
                        "source": f"{fact.source_file}:{fact.source_row}",
                    }
                )
    q3_share = []
    for label, value in (
        (context.prior_period_label, context.monthly.get("prior_share_pct")),
        (context.period_label, context.monthly.get("current_share_pct")),
    ):
        if value is not None:
            q3_share.append({"label": label, "value": float(value)})
    return {
        "revenue_cost": revenue_cost,
        "margin": margin_data,
        "monthly_revenue": monthly,
        "q3_share": q3_share,
    }


def analysis_payload(
    state: AnalysisState,
    dataset: Dataset | None,
    *,
    include_report: bool = False,
) -> dict[str, Any]:
    payload = to_jsonable(state)
    payload.pop("report_html", None)
    payload.pop("report_markdown", None)
    payload.pop("report_appendix_html", None)
    payload["progress_done"] = state.progress_done
    payload["progress_total"] = state.progress_total
    payload["risk_counts"] = _risk_counts(state)
    payload["evidence_count"] = len(state.evidence)
    payload["pending_count"] = len(state.pending_questions)
    payload["report_available"] = bool(state.report_markdown)
    if dataset is not None:
        try:
            payload["period_days"] = compute_metrics(dataset).days
        except Exception:
            payload["period_days"] = None
    else:
        payload["period_days"] = None
    payload["next_action"] = (
        "run"
        if state.status in {"created", "running"}
        else "review"
        if state.status == "paused"
        else "reupload"
        if state.status == "blocked"
        else "report"
        if state.status == "completed"
        else "reset"
    )
    payload["current_step"] = (
        state.steps[state.current_step_index].model_dump(mode="json")
        if 0 <= state.current_step_index < len(state.steps)
        else None
    )
    payload["metrics"] = [metric_payload(metric) for metric in state.metrics]
    if dataset is not None:
        payload["dataset"] = dataset_payload(dataset)
        payload["charts"] = _charts(dataset, state)
    else:
        payload["dataset"] = None
        payload["charts"] = {"revenue_cost": [], "margin": [], "monthly_revenue": [], "q3_share": []}
    if include_report:
        payload["report_html"] = state.report_html
        payload["report_appendix_html"] = state.report_appendix_html
        payload["report_markdown"] = state.report_markdown
    return payload


def analysis_summary(state: AnalysisState) -> dict[str, Any]:
    return {
        "id": state.id,
        "company": state.company,
        "dataset_id": state.dataset_id,
        "goal": state.goal,
        "status": state.status,
        "status_label": state.status_label,
        "period_label": state.period_label,
        "updated_at": state.updated_at.isoformat(),
        "progress_done": state.progress_done,
        "progress_total": state.progress_total,
        "finding_count": len(state.findings),
        "pending_count": len(state.pending_questions),
        "report_available": bool(state.report_markdown),
    }
