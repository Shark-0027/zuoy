from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from finguard.demo import SAMPLE_FILE_NAME
from finguard.main import create_app


def _bootstrap(client: TestClient) -> dict:
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    return response.json()


def _complete_demo_analysis(client: TestClient) -> dict:
    data = _bootstrap(client)
    analysis_id = data["analysis"]["id"]
    response = client.post(
        f"/api/analyses/{analysis_id}/review",
        json={"decision": "confirm_limitations", "note": "测试确认限制", "auto_continue": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    return response.json()


def test_health_and_bootstrap_without_model(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["mode"] == "local_demo"
    assert health.json()["llm_configured"] is False

    data = _bootstrap(client)
    analysis = data["analysis"]
    assert analysis["status"] == "paused"
    assert analysis["period_label"] == "2025 前三季度"
    assert analysis["risk_counts"] == {"high": 0, "medium": 5, "low": 1, "total": 6}
    assert len(analysis["metrics"]) == 19
    assert data["config"]["llm"]["configured"] is False
    assert "api_key" not in str(data["config"]["llm"]).lower() or data["config"]["llm"]["api_key_configured"] is False


def test_report_is_generated_only_after_review_and_is_consistent(client):
    data = _bootstrap(client)
    analysis_id = data["analysis"]["id"]
    blocked = client.get(f"/api/analyses/{analysis_id}/report")
    assert blocked.status_code == 409

    completed = _complete_demo_analysis(client)
    assert completed["report_available"] is True
    assert completed["model_status"] == "local"
    assert all(step["status"] == "completed" for step in completed["steps"])

    report = client.get(f"/api/analyses/{analysis_id}/report")
    assert report.status_code == 200
    payload = report.json()
    markdown = payload["markdown"]
    assert "## 10 分析限制" in markdown
    assert "63.7%" in markdown
    assert "81.7 天" in markdown
    assert "A-01" in markdown and "候选" in markdown
    assert "存在舞弊" not in markdown
    html = payload["html"]
    assert "报告草稿" in html
    assert "验证" not in html or "待核验" in html

    download = client.get(f"/api/analyses/{analysis_id}/report.md")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert "## 01 执行摘要" in download.text
    assert len(download.content) > 1000


def test_scenario_api_returns_sensitivity_and_traceable_baseline(client):
    data = _bootstrap(client)
    analysis_id = data["analysis"]["id"]
    response = client.post(
        f"/api/analyses/{analysis_id}/scenario",
        json={
            "revenue_change_pct": 4,
            "cost_rate_change_pp": -1,
            "selling_expense_change_pct": 6,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scenario"]["is_simulation"] is True
    assert payload["scenario"]["baseline"]["net_profit"] == pytest.approx(12_682_500)
    assert len(payload["sensitivity"]) == 3
    assert len(payload["sensitivity"][0]["cells"]) == 3
    assert payload["scenario"]["baseline"]["evidence_ids"]
    assert any("不是预算" in item for item in payload["scenario"]["limitations"])


def test_sample_xlsx_upload_mapping_and_analysis(client, temp_settings):
    sample_path = temp_settings.demo_dir / SAMPLE_FILE_NAME
    assert sample_path.exists()
    with sample_path.open("rb") as handle:
        response = client.post(
            "/api/uploads",
            files={
                "files": (
                    sample_path.name,
                    handle.read(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"company": "星禾商贸有限公司"},
        )
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert dataset["company"] == "星禾商贸有限公司"
    assert dataset["facts_count"] > 0
    assert any(item["raw_item"] == "暂收款" for item in dataset["mapping"]["items"])

    mapping = client.patch(
        f"/api/uploads/{dataset['id']}/mapping",
        json={"item_overrides": {"暂收款": None}},
    )
    assert mapping.status_code == 200, mapping.text
    assert mapping.json()["blocker_count"] == 0

    created = client.post(
        "/api/analyses",
        json={"dataset_id": dataset["id"], "goal": "测试上传数据", "auto_run": False},
    )
    assert created.status_code == 201
    analysis_id = created.json()["id"]
    run = client.post(f"/api/analyses/{analysis_id}/run-to-pause")
    assert run.status_code == 200
    assert run.json()["status"] == "paused"
    assert run.json()["pending_count"] >= 1
    assert run.json()["dataset"]["is_demo"] is False


def test_incomplete_upload_blocks_analysis(client):
    csv = "statement,period,item,value,unit\nincome,2025-9M,营业收入,1000000,元\n"
    response = client.post(
        "/api/uploads",
        files={"files": ("incomplete.csv", csv.encode("utf-8"), "text/csv")},
        data={"company": "不完整公司"},
    )
    assert response.status_code == 201
    dataset = response.json()
    assert dataset["blocker_count"] > 0
    created = client.post("/api/analyses", json={"dataset_id": dataset["id"], "auto_run": False})
    analysis_id = created.json()["id"]
    run = client.post(f"/api/analyses/{analysis_id}/run-to-pause")
    assert run.status_code == 200
    assert run.json()["status"] == "blocked"
    assert run.json()["pending_count"] >= 1


def test_config_status_never_exposes_secret(temp_settings):
    # Use a configured-looking Settings object to ensure the status serializer
    # does not echo the key. No network call is made by this endpoint.
    from dataclasses import replace

    configured = replace(
        temp_settings,
        llm_base_url="https://example.invalid/v1",
        llm_api_key="super-secret-key",
        llm_model="demo-model",
    )
    app = create_app(configured)
    with TestClient(app) as configured_client:
        response = configured_client.get("/api/config/status")
        body = response.text
        assert response.status_code == 200
        assert "super-secret-key" not in body
        assert response.json()["llm"]["configured"] is True
        assert response.json()["llm"]["api_key_configured"] is True
