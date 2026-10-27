from __future__ import annotations

from dataclasses import replace

import httpx

from finguard.agent.runtime import AgentRuntime
from finguard.analysis.formatting import metric_display
from finguard.analysis.metrics import compute_metrics
from finguard.analysis.tools import verify_report_text
from finguard.config import Settings
from finguard.llm import LOCAL_NARRATIVE, NarrativeClient
from finguard.models import ReviewDecision


def test_agent_really_pauses_and_then_reports(demo_dataset, temp_settings):
    runtime = AgentRuntime(NarrativeClient(temp_settings))
    state = runtime.create_analysis(demo_dataset)
    state = runtime.run_to_pause(state, demo_dataset)
    assert state.status == "paused"
    assert state.pending_questions
    assert state.steps[5].status == "waiting_review"
    assert state.report_markdown == ""
    assert state.model_status == "local"

    state = runtime.review(
        state,
        demo_dataset,
        ReviewDecision(decision="confirm_limitations", note="教学演示确认"),
    )
    state = runtime.run_to_pause(state, demo_dataset)
    assert state.status == "completed"
    assert state.report_markdown
    assert state.report_html
    assert all(step.status == "completed" for step in state.steps)
    assert state.verified_conclusions
    assert state.unverified_conclusions
    assert state.cannot_conclude


def test_report_contains_key_metric_values_and_no_unsupported_claim(demo_dataset, temp_settings):
    runtime = AgentRuntime(NarrativeClient(temp_settings))
    state = runtime.run_to_pause(runtime.create_analysis(demo_dataset), demo_dataset)
    state = runtime.review(state, demo_dataset, ReviewDecision(decision="confirm_limitations"))
    state = runtime.run_to_pause(state, demo_dataset)
    context = compute_metrics(demo_dataset)
    for metric_id in ("revenue", "gross_margin", "net_profit", "operating_cash_flow"):
        metric = context.metric(metric_id)
        assert metric_display(metric)["value"] in state.report_markdown
    assert verify_report_text(state.report_markdown, state, state.findings, context) == []


def test_local_model_mode_makes_no_remote_call(temp_settings):
    narrative = NarrativeClient(temp_settings)
    assert narrative.configured is False
    outcome = narrative.generate(metrics=[], findings=[], pending=[])
    assert outcome.status == "local"
    assert outcome.text == LOCAL_NARRATIVE


def test_configured_model_failure_falls_back_safely(demo_dataset, monkeypatch):
    base = Settings.from_env()
    configured = replace(
        base,
        llm_base_url="https://example.invalid/v1",
        llm_api_key="super-secret-key",
        llm_model="demo-model",
        llm_timeout_seconds=1,
    )

    class BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused super-secret-key")

    monkeypatch.setattr(httpx, "Client", BoomClient)
    narrative = NarrativeClient(configured)
    outcome = narrative.generate(metrics=[], findings=[], pending=[])
    assert outcome.status == "remote_failed_fallback"
    assert outcome.text == LOCAL_NARRATIVE
    assert "super-secret-key" not in outcome.error
    assert "[redacted]" in outcome.error or "connection refused" in outcome.error


def test_model_narrative_with_ungrounded_digits_is_rejected(monkeypatch, temp_settings):
    configured = replace(
        temp_settings,
        llm_base_url="https://example.invalid/v1",
        llm_api_key="secret",
        llm_model="demo-model",
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "本期收入增长 8.6%，建议核查。"}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    narrative = NarrativeClient(configured)
    outcome = narrative.generate(metrics=[], findings=[], pending=[])
    assert outcome.status == "remote_rejected_fallback"
    assert outcome.text == LOCAL_NARRATIVE
    assert "数字" in outcome.error


def test_agent_pauses_when_key_metric_is_not_computable(demo_dataset, temp_settings):
    from finguard.ingestion.quality import build_quality_issues

    broken = demo_dataset.model_copy(deep=True)
    broken.facts = [
        fact
        for fact in broken.facts
        if not (fact.field == "accounts_receivable" and fact.period_raw == "2024-09-30")
    ]
    broken.quality = build_quality_issues(broken)
    runtime = AgentRuntime(NarrativeClient(temp_settings))
    state = runtime.run_to_pause(runtime.create_analysis(broken), broken)
    assert state.status == "paused"
    assert state.report_markdown == ""
    assert any("关键指标不可计算" in question.title for question in state.pending_questions)


def _configured_settings(base: Settings) -> Settings:
    from dataclasses import replace

    return replace(
        base,
        llm_base_url="https://example.invalid/v1",
        llm_api_key="secret",
        llm_model="demo-model",
        llm_timeout_seconds=1,
    )


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self.text}}]}


class _FakeClient:
    def __init__(self, response: _FakeResponse, *args, **kwargs):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return self.response


def test_llm_insight_success_is_structured_and_grounded(monkeypatch, temp_settings):
    configured = _configured_settings(temp_settings)
    text = """```json
    {
      "summary": "收入与成本方向不一致，毛利空间收缩；现金流回款仍需业务资料验证。",
      "signals": ["收入增长而成本上升", "利润现金含量下降", "营运资本占用上升"],
      "questions": ["应收账款账龄与期后回款", "其他应收款对手方与性质"],
      "limitations": ["未读取合同、发票或银行流水", "单期信号不能证明连续期间条件"]
    }
    ```"""
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: _FakeClient(_FakeResponse(text)))
    client = NarrativeClient(configured)
    outcome = client.generate_insight(metrics=[], findings=[], pending=[])
    assert outcome.status == "remote_success"
    assert outcome.payload is not None
    assert len(outcome.payload["signals"]) == 3
    assert "未读取合同" in outcome.payload["limitations"][0]


def test_llm_insight_rejects_digits_and_banned_phrases(monkeypatch, temp_settings):
    configured = _configured_settings(temp_settings)
    text = '{"summary":"收入与成本方向不一致，可能存在舞弊风险，需要业务资料核实。","signals":["收入上升"],"questions":["核查收入"],"limitations":["数据有限"]}'
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: _FakeClient(_FakeResponse(text)))
    client = NarrativeClient(configured)
    outcome = client.generate_insight(metrics=[], findings=[], pending=[])
    assert outcome.status == "remote_failed_fallback"
    assert outcome.payload is None
    assert "数字" in outcome.error or "定性表述" in outcome.error


def test_llm_endpoint_resolution_supports_common_gateways(temp_settings):
    from dataclasses import replace

    client = NarrativeClient(replace(temp_settings, llm_base_url="https://example.invalid/v1"))
    assert client._endpoint() == "https://example.invalid/v1/chat/completions"

    client = NarrativeClient(replace(temp_settings, llm_base_url="https://example.invalid"))
    assert client._endpoint() == "https://example.invalid/v1/chat/completions"

    client = NarrativeClient(
        replace(
            temp_settings,
            llm_base_url="https://ark.cn-beijing.volces.com/api/coding",
        )
    )
    assert client._endpoint() == "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"

    client = NarrativeClient(
        replace(
            temp_settings,
            llm_base_url="https://example.invalid/api",
            llm_completions_path="/v3/chat/completions",
        )
    )
    assert client._endpoint() == "https://example.invalid/api/v3/chat/completions"


def test_llm_insight_endpoint_requires_metrics(client):
    data = client.get("/api/bootstrap").json()
    analysis_id = data["analysis"]["id"]
    state = client.get(f"/api/analyses/{analysis_id}").json()
    assert state["metrics"]  # the demo run has already computed metrics
    response = client.post(f"/api/analyses/{analysis_id}/llm-insight")
    # Without a configured provider this must fail explicitly, not fall back to a fake AI result.
    assert response.status_code == 409
    assert "未配置远程模型" in response.json()["detail"]


def test_llm_connection_test_reports_without_financial_data(monkeypatch, temp_settings):
    configured = _configured_settings(temp_settings)
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: _FakeClient(_FakeResponse("OK")))
    outcome = NarrativeClient(configured).test_connection()
    assert outcome.ok is True
    assert outcome.status == "success"
    assert outcome.latency_ms is not None
    assert outcome.error == ""

