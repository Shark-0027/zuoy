from __future__ import annotations

from decimal import Decimal

import pytest

from finguard.analysis.metrics import compute_metrics
from finguard.analysis.rules import detect_findings


def test_demo_metrics_are_computed_from_source_data(demo_dataset):
    context = compute_metrics(demo_dataset)

    assert context.period_label == "2025 前三季度"
    assert context.prior_period_label == "2024 前三季度"
    assert context.days == 273

    revenue = context.metric("revenue")
    gross_margin = context.metric("gross_margin")
    net_profit = context.metric("net_profit")
    ocf = context.metric("operating_cash_flow")
    ocf_ratio = context.metric("ocf_to_net_profit")
    ar_growth = context.metric("ar_growth")
    ar_days = context.metric("ar_turnover_days")
    sep_share = context.metric("september_revenue_share")
    wc_drag = context.metric("working_capital_drag")
    inventory_days = context.metric("inventory_turnover_days")

    assert int(revenue.value) == 128_400_000
    assert int(revenue.prior) == 118_230_000
    assert float(revenue.delta) == pytest.approx(8.6019, abs=0.001)
    assert float(gross_margin.value) == pytest.approx(31.4019, abs=0.001)
    assert float(gross_margin.delta) == pytest.approx(-2.6842, abs=0.001)
    assert int(net_profit.value) == 12_682_500
    assert int(ocf.value) == 9_860_000
    assert float(ocf_ratio.value) == pytest.approx(0.7774, abs=0.0001)
    assert float(ar_growth.value) == pytest.approx(38.2014, abs=0.001)
    assert float(ar_days.value) == pytest.approx(81.6874, abs=0.01)
    assert float(sep_share.value) == pytest.approx(63.6831, abs=0.001)
    assert float(sep_share.prior) == pytest.approx(36.1926, abs=0.001)
    assert float(sep_share.delta) == pytest.approx(27.4905, abs=0.001)
    assert int(wc_drag.value) == 16_100_000
    assert inventory_days.status == "not_computable"
    assert inventory_days.value is None


def test_findings_are_candidates_when_continuity_is_missing(demo_dataset):
    context = compute_metrics(demo_dataset)
    findings = {finding.id: finding for finding in detect_findings(context)}

    assert set(findings) == {"A-01", "A-02", "A-03", "A-04", "A-05", "A-06"}
    assert findings["A-01"].status == "candidate"
    assert findings["A-01"].risk_level == "medium"
    assert "连续" in findings["A-01"].limitation
    assert findings["A-02"].status == "candidate"
    assert findings["A-02"].risk_level == "medium"
    assert findings["A-03"].status == "triggered"
    assert findings["A-04"].status == "needs_evidence"
    assert findings["A-05"].status == "triggered"
    assert "63.7%" in findings["A-05"].metric_value
    assert "+27.5pp" in findings["A-05"].metric_sub
    assert findings["A-06"].risk_level == "low"
    assert findings["A-06"].status == "needs_evidence"
    assert not any(finding.risk_level == "high" for finding in findings.values())


def test_missing_inventory_opening_balance_is_not_zero(demo_dataset):
    context = compute_metrics(demo_dataset)
    metric = context.metric("inventory_turnover_days")
    assert metric.status == "not_computable"
    assert metric.value is None
    assert "补 0" in metric.limitation or "平均存货" in metric.limitation


def test_benign_data_does_not_produce_findings(demo_dataset):

    values = {
        ("revenue", "2025-9M"): Decimal(100_000_000),
        ("revenue", "2024-9M"): Decimal(95_000_000),
        ("cogs", "2025-9M"): Decimal(59_000_000),
        ("cogs", "2024-9M"): Decimal(55_000_000),
        ("selling_expenses", "2025-9M"): Decimal(10_000_000),
        ("selling_expenses", "2024-9M"): Decimal(9_500_000),
        ("net_profit", "2025-9M"): Decimal(15_000_000),
        ("net_profit", "2024-9M"): Decimal(14_000_000),
        ("operating_cash_flow", "2025-9M"): Decimal(16_000_000),
        ("operating_cash_flow", "2024-9M"): Decimal(13_000_000),
        ("accounts_receivable", "2025-09-30"): Decimal(20_000_000),
        ("accounts_receivable", "2024-09-30"): Decimal(19_000_000),
        ("other_receivables", "2025-09-30"): Decimal(2_000_000),
        ("other_receivables", "2024-09-30"): Decimal(1_900_000),
        ("total_assets", "2025-09-30"): Decimal(100_000_000),
        ("total_assets", "2024-09-30"): Decimal(95_000_000),
        ("revenue", "2025-07"): Decimal(8_000_000),
        ("revenue", "2025-08"): Decimal(8_000_000),
        ("revenue", "2025-09"): Decimal(8_000_000),
        ("revenue", "2024-07"): Decimal(7_600_000),
        ("revenue", "2024-08"): Decimal(7_600_000),
        ("revenue", "2024-09"): Decimal(7_600_000),
    }
    benign = demo_dataset.model_copy(deep=True)
    for fact in benign.facts:
        key = (fact.field, fact.period_raw)
        if key in values:
            fact.value = values[key]
            fact.raw_value = str(values[key])
    context = compute_metrics(benign)
    findings = detect_findings(context)
    assert findings == []
