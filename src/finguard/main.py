from __future__ import annotations

import os
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from finguard.agent.runtime import AgentRuntime, AgentStateError
from finguard.analysis.metrics import compute_metrics
from finguard.analysis.scenario import ScenarioUnavailable, run_scenario
from finguard.config import Settings
from finguard.demo import (
    DEMO_COMPANY,
    DEMO_GOAL,
    build_demo_dataset,
    ensure_sample_upload,
    parse_sample_upload,
)
from finguard.ingestion.parser import apply_item_overrides, build_file_meta, parse_files
from finguard.llm import NarrativeClient
from finguard.models import AnalysisState, Dataset, ReviewDecision, ScenarioParams, utcnow
from finguard.serialization import analysis_payload, analysis_summary, dataset_payload, to_jsonable
from finguard.store import Store

API_VERSION = "0.1.0"


class AnalysisCreateRequest(BaseModel):
    dataset_id: str
    goal: str | None = None
    auto_run: bool = False


class MappingPatchRequest(BaseModel):
    item_overrides: dict[str, str | None] = Field(default_factory=dict)


class ReviewRequest(ReviewDecision):
    auto_continue: bool = False


class ScenarioRequest(ScenarioParams):
    pass


def _settings_from(app: FastAPI) -> Settings:
    return app.state.settings


def _store(app: FastAPI) -> Store:
    return app.state.store


def _runtime(app: FastAPI) -> AgentRuntime:
    return app.state.runtime


def _get_dataset(app: FastAPI, dataset_id: str) -> Dataset:
    dataset = _store(app).get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="数据集不存在或已被删除。")
    return dataset


def _get_analysis(app: FastAPI, analysis_id: str) -> tuple[AnalysisState, Dataset]:
    state = _store(app).get_analysis(analysis_id)
    if state is None:
        raise HTTPException(status_code=404, detail="分析任务不存在。")
    dataset = _store(app).get_dataset(state.dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="分析对应的数据集不存在。")
    return state, dataset


def _save_state(app: FastAPI, state: AnalysisState) -> None:
    _store(app).upsert_analysis(state)


def _config_payload(app: FastAPI) -> dict[str, object]:
    settings = _settings_from(app)
    narrative = NarrativeClient(settings)
    ensure_sample_upload(settings)
    return {
        "app": {"name": "FinGuard", "version": API_VERSION, "mode": "local_demo"},
        "auth": {
            "mode": "local_demo",
            "authenticated": False,
            "note": "本地单用户演示模式，不是生产级身份认证；未接入 Supabase Auth。",
        },
        "storage": {
            "mode": "local_filesystem",
            "data_dir": str(settings.data_dir) if settings.debug else "local data directory",
            "store_file": str(settings.store_file) if settings.debug else "local store",
            "note": "上传文件保存在本机；运行状态使用本地 JSON，不上云。",
        },
        "llm": narrative.status(),
        "uploads": {
            "max_mb": settings.max_upload_mb,
            "max_files": settings.max_files,
            "formats": ["CSV", "XLSX", "PDF（基础文本型）"],
        },
        "pdf": {
            "supported": True,
            "note": "支持带可提取文本的 PDF；扫描件、图片型 PDF 与复杂跨页表格需 OCR 或人工录入。",
        },
        "demo": {
            "company": DEMO_COMPANY,
            "sample_name": "星禾商贸有限公司",
            "sample_download": "/api/demo/sample.xlsx",
            "sample_import": "/api/demo/sample/import",
        },
    }


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or Settings.from_env()
    settings.ensure_dirs()
    narrative_client = NarrativeClient(settings)
    runtime = AgentRuntime(narrative_client)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.narrative = narrative_client
        app.state.runtime = runtime
        app.state.store = Store(settings)
        store: Store = app.state.store
        # Demo is rebuilt on every startup so code/data changes are reflected.
        demo_dataset = build_demo_dataset(settings)
        store.datasets[demo_dataset.id] = demo_dataset
        demo_state = runtime.create_analysis(demo_dataset, DEMO_GOAL)
        demo_state = runtime.run_to_pause(demo_state, demo_dataset)
        store.analyses[demo_state.id] = demo_state
        if store.active_analysis_id not in store.analyses:
            store.active_analysis_id = demo_state.id
        ensure_sample_upload(settings)
        app.state.demo_analysis_id = demo_state.id
        yield

    app = FastAPI(
        title="FinGuard API",
        version=API_VERSION,
        description="Evidence-based financial diagnosis Agent for local classroom demonstrations.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response

    @app.exception_handler(AgentStateError)
    async def agent_error_handler(_request: Request, exc: AgentStateError):
        return JSONResponse(status_code=409, content={"detail": str(exc), "code": "agent_state"})

    @app.exception_handler(ScenarioUnavailable)
    async def scenario_error_handler(_request: Request, exc: ScenarioUnavailable):
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "code": "scenario_unavailable", "missing": exc.missing},
        )

    @app.get("/api/health")
    def health(request: Request):
        app = request.app
        settings = _settings_from(app)
        store = _store(app)
        return {
            "status": "ok",
            "version": API_VERSION,
            "mode": "local_demo",
            "llm_configured": settings.llm_configured,
            "datasets": len(store.datasets),
            "analyses": len(store.analyses),
            "time": utcnow().isoformat(),
        }

    @app.get("/api/config/status")
    def config_status(request: Request):
        return _config_payload(request.app)

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        app = request.app
        store = _store(app)
        state = store.latest_analysis()
        if state is None:
            raise HTTPException(status_code=503, detail="演示分析尚未初始化。")
        dataset = store.get_dataset(state.dataset_id)
        if dataset is None:
            raise HTTPException(status_code=503, detail="分析数据集缺失。")
        analyses = [analysis_summary(item) for item in store.list_analyses()]
        return {
            "active_analysis_id": state.id,
            "analysis": analysis_payload(state, dataset),
            "dataset": dataset_payload(dataset),
            "analyses": analyses,
            "config": _config_payload(app),
            "demo": {
                "company": DEMO_COMPANY,
                "sample_download": "/api/demo/sample.xlsx",
                "sample_import": "/api/demo/sample/import",
            },
        }

    @app.get("/api/analyses")
    def list_analyses(request: Request):
        return {"items": [analysis_summary(state) for state in _store(request.app).list_analyses()]}

    @app.get("/api/analyses/{analysis_id}")
    def get_analysis(analysis_id: str, request: Request):
        state, dataset = _get_analysis(request.app, analysis_id)
        return analysis_payload(state, dataset)

    @app.get("/api/datasets/{dataset_id}")
    def get_dataset(dataset_id: str, request: Request):
        return dataset_payload(_get_dataset(request.app, dataset_id))

    @app.post("/api/uploads", status_code=201)
    async def upload_files(
        request: Request,
        files: list[UploadFile] = File(...),
        company: str = Form("未命名企业 · 上传数据"),
    ):
        app = request.app
        settings = _settings_from(app)
        if not files:
            raise HTTPException(status_code=400, detail="请至少选择一个文件。")
        if len(files) > settings.max_files:
            raise HTTPException(status_code=413, detail=f"一次最多上传 {settings.max_files} 个文件。")
        allowed = {".csv", ".xlsx", ".pdf"}
        upload_dataset_id = f"UP-{uuid.uuid4().hex[:10].upper()}"
        destination = settings.upload_dir / upload_dataset_id
        destination.mkdir(parents=True, exist_ok=False)
        specs = []
        try:
            for index, upload in enumerate(files):
                original_name = Path(upload.filename or f"file-{index}").name
                suffix = Path(original_name).suffix.lower()
                if suffix not in allowed:
                    raise HTTPException(
                        status_code=415,
                        detail=f"不支持的格式：{original_name}。当前支持 CSV、XLSX 和基础文本型 PDF。",
                    )
                content = await upload.read()
                if len(content) > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{original_name} 超过 {settings.max_upload_mb} MB 限制。",
                    )
                stored_name = f"{index:02d}-{uuid.uuid4().hex[:8]}{suffix}"
                path = destination / stored_name
                path.write_bytes(content)
                meta = build_file_meta(path, original_name, upload.content_type or "")
                meta.stored_name = stored_name
                specs.append((path, meta))
            dataset = parse_files(
                specs,
                dataset_id=upload_dataset_id,
                company=(company or "未命名企业 · 上传数据").strip()[:120],
                is_demo=False,
                source_label=f"本地导入 · {len(specs)} 个文件",
            )
            _store(app).upsert_dataset(dataset)
            return dataset_payload(dataset)
        except HTTPException:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise HTTPException(status_code=422, detail=f"文件解析失败：{str(exc)[:300]}") from exc

    @app.patch("/api/uploads/{dataset_id}/mapping")
    def update_mapping(
        dataset_id: str,
        body: MappingPatchRequest,
        request: Request,
    ):
        app = request.app
        dataset = _get_dataset(app, dataset_id)
        if dataset.is_demo:
            raise HTTPException(status_code=403, detail="演示数据集的映射为只读。")
        existing = [analysis for analysis in _store(app).analyses.values() if analysis.dataset_id == dataset_id]
        if existing:
            raise HTTPException(status_code=409, detail="该数据集已创建分析，不能再修改映射；请重新上传。")
        try:
            dataset = apply_item_overrides(dataset, body.item_overrides)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _store(app).upsert_dataset(dataset)
        return dataset_payload(dataset)

    @app.post("/api/analyses", status_code=201)
    def create_analysis(body: AnalysisCreateRequest, request: Request):
        app = request.app
        dataset = _get_dataset(app, body.dataset_id)
        state = _runtime(app).create_analysis(dataset, body.goal)
        if body.auto_run:
            state = _runtime(app).run_to_pause(state, dataset)
        _store(app).upsert_analysis(state)
        _store(app).set_active(state.id)
        return analysis_payload(state, dataset)

    @app.post("/api/analyses/{analysis_id}/advance")
    def advance_analysis(analysis_id: str, request: Request):
        app = request.app
        state, dataset = _get_analysis(app, analysis_id)
        state = _runtime(app).advance(state, dataset)
        _save_state(app, state)
        return analysis_payload(state, dataset)

    @app.post("/api/analyses/{analysis_id}/run-to-pause")
    def run_to_pause(analysis_id: str, request: Request):
        app = request.app
        state, dataset = _get_analysis(app, analysis_id)
        state = _runtime(app).run_to_pause(state, dataset)
        _save_state(app, state)
        return analysis_payload(state, dataset)

    @app.post("/api/analyses/{analysis_id}/review")
    def review_analysis(analysis_id: str, body: ReviewRequest, request: Request):
        app = request.app
        state, dataset = _get_analysis(app, analysis_id)
        decision = ReviewDecision(decision=body.decision, note=body.note)
        state = _runtime(app).review(state, dataset, decision)
        if body.auto_continue and state.status == "running":
            state = _runtime(app).run_to_pause(state, dataset)
        _save_state(app, state)
        return analysis_payload(state, dataset)

    @app.post("/api/analyses/{analysis_id}/reset")
    def reset_analysis(analysis_id: str, request: Request):
        app = request.app
        state, dataset = _get_analysis(app, analysis_id)
        state = _runtime(app).reset(state, dataset)
        _save_state(app, state)
        _store(app).set_active(state.id)
        return analysis_payload(state, dataset)

    @app.post("/api/analyses/{analysis_id}/scenario")
    def scenario(analysis_id: str, body: ScenarioRequest, request: Request):
        app = request.app
        state, dataset = _get_analysis(app, analysis_id)
        params = ScenarioParams(
            revenue_change_pct=body.revenue_change_pct,
            cost_rate_change_pp=body.cost_rate_change_pp,
            selling_expense_change_pct=body.selling_expense_change_pct,
        )
        result = _runtime(app).apply_scenario(state, dataset, params)
        _save_state(app, state)
        context = compute_metrics(dataset)
        sensitivity_rows = []
        for revenue_change in (-5, 0, 5):
            cells = []
            for cost_change in (-1, 0, 1):
                cell = run_scenario(
                    context,
                    ScenarioParams(
                        revenue_change_pct=revenue_change,
                        cost_rate_change_pp=cost_change,
                        selling_expense_change_pct=params.selling_expense_change_pct,
                    ),
                )
                cells.append({"net_profit": cell.net_profit, "delta_pct": cell.delta_pct})
            sensitivity_rows.append({"revenue_change_pct": revenue_change, "cells": cells})
        return {
            "scenario": to_jsonable(result),
            "sensitivity": sensitivity_rows,
            "analysis": analysis_payload(state, dataset),
        }

    @app.post("/api/analyses/{analysis_id}/activate")
    def activate_analysis(analysis_id: str, request: Request):
        app = request.app
        state, dataset = _get_analysis(app, analysis_id)
        _store(app).set_active(state.id)
        return analysis_payload(state, dataset)

    @app.post("/api/analyses/{analysis_id}/llm-insight")
    def generate_llm_insight(analysis_id: str, request: Request):
        app = request.app
        state, dataset = _get_analysis(app, analysis_id)
        insight = _runtime(app).generate_llm_insight(state, dataset)
        _save_state(app, state)
        return {"insight": to_jsonable(insight), "analysis": analysis_payload(state, dataset)}

    @app.post("/api/config/test-llm")
    def test_llm(request: Request):
        app = request.app
        outcome = NarrativeClient(_settings_from(app)).test_connection()
        payload = {
            "ok": outcome.ok,
            "status": outcome.status,
            "model": outcome.model,
            "latency_ms": outcome.latency_ms,
            "error": outcome.error,
        }
        # A failed test is still a valid API response; the frontend shows the reason.
        return payload

    @app.get("/api/analyses/{analysis_id}/report")
    def get_report(analysis_id: str, request: Request):
        app = request.app
        state, dataset = _get_analysis(app, analysis_id)
        if not state.report_markdown:
            raise HTTPException(status_code=409, detail="报告尚未生成；请先完成证据核验并运行报告步骤。")
        return {
            "analysis_id": state.id,
            "available": True,
            "markdown": state.report_markdown,
            "html": state.report_html,
            "appendix_html": state.report_appendix_html,
            "status": state.status,
            "status_label": state.status_label,
            "model_status": state.model_status,
            "generated_at": state.updated_at.isoformat(),
            "download_url": f"/api/analyses/{state.id}/report.md",
        }

    @app.get("/api/analyses/{analysis_id}/report.md")
    def download_report(analysis_id: str, request: Request):
        app = request.app
        state, _dataset = _get_analysis(app, analysis_id)
        if not state.report_markdown:
            raise HTTPException(status_code=409, detail="报告尚未生成。")
        from urllib.parse import quote

        filename = f"FinGuard_{state.id}_诊断报告.md"
        return Response(
            content=state.report_markdown.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=FinGuard_report.md; filename*=UTF-8''{quote(filename)}"
            },
        )

    @app.post("/api/demo/reset")
    def reset_demo(request: Request):
        app = request.app
        store = _store(app)
        dataset = store.get_dataset("demo-chl")
        if dataset is None:
            dataset = build_demo_dataset(_settings_from(app))
            store.datasets[dataset.id] = dataset
        for analysis_id, analysis in list(store.analyses.items()):
            if analysis.dataset_id == dataset.id:
                store.analyses.pop(analysis_id, None)
        state = _runtime(app).create_analysis(dataset, DEMO_GOAL)
        state = _runtime(app).run_to_pause(state, dataset)
        store.analyses[state.id] = state
        store.set_active(state.id)
        return analysis_payload(state, dataset)

    @app.post("/api/demo/sample/import")
    def import_sample(request: Request):
        app = request.app
        dataset = parse_sample_upload(_settings_from(app))
        _store(app).upsert_dataset(dataset)
        return dataset_payload(dataset)

    @app.get("/api/demo/sample.xlsx")
    def download_sample(request: Request):
        app = request.app
        path = ensure_sample_upload(_settings_from(app))
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="星禾商贸_示例上传.xlsx",
        )

    # Serve the existing high-fidelity frontend at the site root.
    app.mount("/", StaticFiles(directory=str(settings.frontend_dir), html=True), name="frontend")
    return app


app = create_app()


def run() -> None:
    host = os.getenv("FINGUARD_HOST", "127.0.0.1")
    port = int(os.getenv("FINGUARD_PORT", "8000"))
    reload_enabled = os.getenv("FINGUARD_RELOAD", "0") == "1"
    uvicorn.run("finguard.main:app", host=host, port=port, reload=reload_enabled)


if __name__ == "__main__":
    run()
