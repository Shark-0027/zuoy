from __future__ import annotations

import pytest

from finguard.analysis.metrics import compute_metrics
from finguard.analysis.scenario import ScenarioUnavailable, build_baseline, run_scenario
from finguard.models import ScenarioParams


def test_scenario_uses_traceable_baseline(demo_dataset):
    context = compute_metrics(demo_dataset)
    baseline = build_baseline(context)
    assert int(baseline.revenue) == 128_400_000
    assert baseline.gross_margin_pct == pytest.approx(31.4019, abs=0.001)
    assert int(baseline.selling_expenses) == 10_030_000
    assert int(baseline.net_profit) == 12_682_500
    assert baseline.tax_rate_pct == pytest.approx(25.0, abs=0.001)
    assert baseline.evidence_ids


def test_scenario_is_deterministic_and_has_no_undeclared_projection(demo_dataset):
    context = compute_metrics(demo_dataset)
    params = ScenarioParams(revenue_change_pct=4, cost_rate_change_pp=-1, selling_expense_change_pct=6)
    result = run_scenario(context, params)

    assert result.net_profit == pytest.approx(14_442_270.0, abs=1.0)
    assert result.delta_pct == pytest.approx(13.88, abs=0.01)
    assert result.is_simulation is True
    assert "模拟营业收入" in result.formula
    assert any("不是预算" in item for item in result.limitations)
    assert result.baseline.evidence_ids


def test_scenario_refuses_missing_foundation(demo_dataset):
    broken = demo_dataset.model_copy(deep=True)
    broken.facts = [
        fact
        for fact in broken.facts
        if not (fact.field == "admin_expenses" and fact.period_raw == "2025-9M")
    ]
    from finguard.ingestion.quality import build_quality_issues

    broken.quality = build_quality_issues(broken)
    context = compute_metrics(broken)
    with pytest.raises(ScenarioUnavailable) as exc:
        build_baseline(context)
    assert "管理费用" in exc.value.missing
