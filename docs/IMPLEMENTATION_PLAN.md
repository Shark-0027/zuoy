# FinGuard 实现路线图（IMPLEMENTATION PLAN）

> 版本：0.2（本地实现版）  
> 日期：2026-10-26  
> 当前阶段：本地演示版已实现并通过 29 项自动化测试  
> 尚未启用：Supabase Auth、云数据库、生产级多租户、扫描 PDF OCR、服务端 PDF 导出  
> 技术基线：Ubuntu · Python 3.12 · uv · FastAPI · Pandas · Pydantic · httpx（OpenAI 兼容调用）· Pytest

---

## 1. 阶段边界

### 1.1 本轮交付（已实现）

- `docs/SPEC.md`、`DESIGN.md`、`IMPLEMENTATION_PLAN.md` 与实现同步；
- `src/finguard/`：FastAPI、数据接入、指标、规则、情景、报告、Agent 运行时；
- `design_preview/`：由 FastAPI 同源托管、连接真实 API 的八视图前端；
- 本地演示数据、第二套可上传 XLSX 示例、测试与 Docker/Render 配置；
- 可选远程模型叙述，未配置时完全本地运行；无生产级认证。

### 1.2 当前仍不做

- 不接入 Supabase Auth、云数据库、生产级多租户；
- 不实现扫描 PDF OCR；
- 不提供服务端 PDF 渲染（使用浏览器打印）；
- 不采购或绑定付费模型服务；远程模型仅通过环境变量可选配置；
- 不自动替用户部署云实例。

### 1.3 已实现与未实现（2026-10-26）

| 能力 | 状态 |
|---|---|
| CSV / XLSX / 基础文本型 PDF 解析 | 已实现 |
| 字段映射、缺失/无效/单位/期间/勾稽检查 | 已实现 |
| 确定性指标、规则、证据链 | 已实现 |
| 真实暂停/确认/继续/重新执行的 Agent | 已实现 |
| 服务端情景模拟与敏感度矩阵 | 已实现 |
| 页面与 Markdown/HTML 报告共源 | 已实现 |
| 课堂投影 / 讲解模式 | 已实现 |
| 可选远程模型叙述与失败回退 | 已实现 |
| 按需 AI 解读、JSON/数字/定性校验 | 已实现 |
| 用户主动触发的模型连接测试 | 已实现 |
| 本地单用户模式 / Docker / Render 配置 | 已实现 |
| Supabase Auth / 云数据库 / 多租户 | 未实现 |
| 扫描 PDF OCR / 服务端 PDF 渲染 | 未实现 |
| 生产级审计、保留策略 UI、备份 | 未实现 |
| 实际云实例部署 | 未执行（需用户云账号） |

### 1.4 阶段出口

设计评审已在实现过程中转化为本地可运行版本。后续 M5/M6 的启动条件：

- [ ] 用户决定是否接入 Supabase Auth / 云数据库；
- [ ] 用户确认模型服务提供方与数据合规边界；
- [ ] 用户决定是否需要扫描 PDF OCR 与服务端 PDF；
- [ ] 真实试用反馈确认当前单用户闭环可用。

---

## 1.5 当前启动方式

```bash
uv sync
uv run finguard
# 打开 http://127.0.0.1:8000
```

测试：`./scripts/test.sh`。容器：`docker compose up --build`。

---

## 2. 目标架构

### 2.1 总体形态

```text
Browser (HTML/CSS/JS)
   │  HTTPS / JSON / SSE
   ▼
FastAPI application
   ├── Auth dependency (Supabase JWT optional)
   ├── Upload & parsing service (Pandas / openpyxl / pdfplumber*)
   ├── Analysis service (metrics, comparisons, rules)
   ├── Agent runtime
   │     ├── Planner (LLM structured output)
   │     ├── Tool registry & executor
   │     ├── Evidence ledger
   │     ├── Verifier
   │     └── Event stream
   ├── Scenario service (deterministic)
   ├── Report service (template + verification)
   └── Audit / health / config
   │
   ├── PostgreSQL / Supabase Postgres (optional)
   ├── Object storage (Supabase Storage or local volume)
   └── Model provider (OpenAI-compatible base_url)
```

*PDF 解析仅在通过质量门槛后进入第一版；否则明确延后，前端保持“规划中”。

### 2.2 技术选择

| 层 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.12 | 生态成熟、类型与异步支持好 |
| 环境 | uv | 快速、锁文件可复现、单工具管理 |
| Web | FastAPI | Pydantic 原生、OpenAPI、SSE 支持 |
| 模板/前端 | 原生 HTML/CSS/JS | 与静态原型一致，无构建链，课堂易于展示 |
| 数据 | Pandas + openpyxl | 表格计算与 Excel 读取成熟 |
| 校验 | Pydantic v2 | 工具参数、API、Agent 输出统一校验 |
| 模型 | OpenAI 兼容 SDK | 适配 DeepSeek V4.1 Flash；可替换 |
| 测试 | Pytest + pytest-asyncio + httpx | 单元、接口、异步 |
| 质量 | Ruff + mypy（可选） | 格式、lint、类型 |
| 数据库 | Supabase Postgres（可选） | Auth/RLS/Storage 一体化；教学部署简单 |
| 本地数据 | SQLite + 本地文件目录 | 无云依赖的课堂演示 |
| 部署 | Docker + Render/Railway/Fly.io | 单容器 + 托管数据库 |

### 2.3 架构原则

1. **确定性计算与语言模型分离**：模型不产生权威数字。
2. **一切结论有证据 ID**：报告断言可追溯到工具运行和原始行。
3. **事件驱动界面**：Agent 进度通过事件流展示，而不是前端计时器。
4. **先单租户本地可跑，再开 Auth/云**：降低课堂演示复杂度。
5. **文件解析失败可恢复**：不因单文件失败丢失全部上下文。
6. **工具白名单**：模型只能调用注册工具，不能执行任意代码/SQL。
7. **规则版本化**：每条异常记录规则版本与阈值。
8. **提示注入按数据处理**：文件内容不进入系统指令层级。

---

## 3. 建议目录结构（未来）

```text
finguard/
├── pyproject.toml
├── uv.lock
├── README.md
├── .env.example
├── Dockerfile
├── compose.yaml
├── docs/
│   ├── SPEC.md
│   ├── DESIGN.md
│   └── IMPLEMENTATION_PLAN.md
├── design_preview/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── src/finguard/
│   ├── main.py
│   ├── config.py
│   ├── api/
│   │   ├── routes_health.py
│   │   ├── routes_uploads.py
│   │   ├── routes_analyses.py
│   │   ├── routes_scenarios.py
│   │   ├── routes_reports.py
│   │   └── deps.py
│   ├── models/
│   │   ├── db.py
│   │   ├── api.py
│   │   ├── domain.py
│   │   └── events.py
│   ├── ingestion/
│   │   ├── readers.py
│   │   ├── mapping.py
│   │   ├── quality.py
│   │   └── normalizers.py
│   ├── analysis/
│   │   ├── metrics.py
│   │   ├── compare.py
│   │   ├── rules/
│   │   │   ├── registry.py
│   │   │   ├── liquidity.py
│   │   │   ├── revenue.py
│   │   │   └── expense.py
│   │   └── evidence.py
│   ├── agent/
│   │   ├── prompts/
│   │   ├── planner.py
│   │   ├── runtime.py
│   │   ├── tools.py
│   │   ├── verifier.py
│   │   └── events.py
│   ├── scenarios/
│   │   └── profit_model.py
│   ├── reporting/
│   │   ├── builder.py
│   │   ├── render_markdown.py
│   │   └── verify.py
│   ├── security/
│   │   ├── auth.py
│   │   ├── redaction.py
│   │   └── limits.py
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── styles.css
└── tests/
    ├── unit/
    ├── integration/
    ├── contract/
    ├── e2e/
    └── fixtures/
        ├── statements_2024_9m.xlsx
        ├── statements_2025_9m.xlsx
        └── malformed/
```

---

## 4. 数据模型（初稿）

### 4.1 核心表

```text
organizations(id, name, created_at)
users(id, org_id, email, role, created_at)
documents(
  id, org_id, uploaded_by, original_name, storage_path,
  mime_type, size_bytes, sha256, status, created_at, expires_at
)
datasets(
  id, org_id, document_id, statement_type, period_start, period_end,
  currency, unit_scale, status, field_mapping_json, quality_score
)
facts(
  id, dataset_id, statement_type, line_item, period, value,
  source_row, source_col, evidence_id
)
analyses(
  id, org_id, dataset_id, goal, status, plan_json, model_meta_json,
  created_at, completed_at
)
analysis_events(
  id, analysis_id, seq, event_type, payload_json, created_at
)
tool_runs(
  id, analysis_id, tool_name, args_json, result_summary_json,
  status, started_at, finished_at, error_code
)
findings(
  id, analysis_id, rule_id, rule_version, severity, title, period,
  metric_name, metric_value, threshold_json, evidence_ids,
  status, limitation, created_at
)
scenarios(
  id, analysis_id, name, params_json, result_json, assumptions_json,
  is_snapshot, created_at
)
reports(
  id, analysis_id, version, status, markdown, evidence_snapshot_json,
  verified_at, created_by, created_at
)
audit_logs(
  id, org_id, actor_id, action, resource_type, resource_id,
  ip_hash, metadata_json, created_at
)
```

### 4.2 金额与期间

- 数据库金额使用 `Numeric(20, 2)`，避免浮点误差；
- 原始文件单位（元/千元/万元）保存在 `unit_scale`，规范化后统一为元；
- 期间使用 `date`，不使用字符串；
- 所有派生指标保存计算公式版本与输入事实 ID；
- 前端格式化只负责展示，不参与计算。

### 4.3 事件模型

```json
{
  "seq": 14,
  "event_type": "tool.completed",
  "analysis_id": "an_01J...",
  "tool_run_id": "tr_01J...",
  "payload": {
    "tool": "detect_anomalies",
    "status": "completed",
    "result_summary": "5 findings, 2 high, 2 medium, 1 low",
    "evidence_ids": ["ev_ar_01", "ev_cf_01"],
    "duration_ms": 184
  },
  "created_at": "2026-10-26T08:00:00Z"
}
```

事件类型：

- `analysis.created`
- `plan.generated`
- `plan.confirmed`
- `step.started`
- `tool.completed` / `tool.failed`
- `evidence.added`
- `analysis.paused`
- `analysis.resumed`
- `report.drafted`
- `report.verified`
- `analysis.completed` / `analysis.failed`

前端只渲染服务端事件；不得用 `setInterval` 伪造进度。

---

## 5. API 草图

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 存活/版本/时间 |
| GET | `/api/config/status` | 模型/DB/Auth 配置状态（无密钥） |
| POST | `/api/uploads` | 上传文件（大小/类型限制） |
| GET | `/api/uploads/{id}` | 文件状态 |
| GET | `/api/uploads/{id}/mapping` | 字段映射建议 |
| PATCH | `/api/uploads/{id}/mapping` | 人工修正映射 |
| GET | `/api/uploads/{id}/quality` | 质量检查结果 |
| POST | `/api/analyses` | 创建分析任务 |
| GET | `/api/analyses/{id}` | 分析摘要 |
| GET | `/api/analyses/{id}/events` | SSE 事件流 |
| POST | `/api/analyses/{id}/confirm-plan` | 确认/修改计划 |
| POST | `/api/analyses/{id}/resume` | 补充信息后继续 |
| GET | `/api/analyses/{id}/metrics` | 指标与口径 |
| GET | `/api/analyses/{id}/findings` | 异常清单 |
| GET | `/api/analyses/{id}/findings/{fid}` | 异常详情 |
| POST | `/api/analyses/{id}/scenarios` | 运行情景 |
| GET | `/api/analyses/{id}/report` | 报告草稿 |
| POST | `/api/analyses/{id}/report/verify` | 触发数值/引用核验 |
| POST | `/api/analyses/{id}/report/export` | 导出 Markdown/PDF |
| DELETE | `/api/analyses/{id}` | 删除及审计 |
| GET | `/api/audit-logs` | 组织审计（Owner） |

约束：

- 所有分析资源按 `org_id` 过滤；
- 上传限制：单文件 20MB、单次 5 个、仅允许 CSV/XLSX（PDF 后评估）；
- SSE 心跳 15s；客户端断开后任务按策略继续或暂停；
- 模型调用超时 60s，工具 30/90s；
- 请求体/响应体限制；
- 错误返回统一 `{code, message, request_id}`，不返回堆栈。

---

## 6. Agent 运行时设计

### 6.1 工具注册表

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    permission: Literal["read", "compute", "write_report"]
    timeout_s: int
    idempotent: bool
```

第一版工具：

| 工具 | 输入 | 输出 | 是否模型可选 |
|---|---|---|---|
| `profile_dataset` | dataset_id | 质量摘要、字段、期间 | 是 |
| `compute_metrics` | dataset_id, metric_ids, periods | 指标值/公式/证据 | 是 |
| `compare_periods` | metric_ids, periods | 同比/环比与说明 | 是 |
| `detect_anomalies` | dataset_id, rule_set | findings + evidence | 是 |
| `fetch_evidence` | finding_id / evidence_ids | 原始行、来源 | 是 |
| `run_scenario` | params + baseline | 结果、假设、限制 | 是（需用户目标或确认） |
| `draft_section` | section, context | 报告片段（非数值权威） | 是 |
| `verify_report` | report_id | 校验结果 | 强制最后一步 |
| `request_user_input` | question, reason | 暂停状态 | 是 |

模型不能直接访问数据库、文件系统、Shell 或任意 HTTP。

### 6.2 规划输出 Schema（示例）

```json
{
  "goal_summary": "解释 2025 前三季度收入增长但净利润下降",
  "assumptions": ["期间为 2025-01-01 至 2025-09-30"],
  "steps": [
    {
      "id": "s1",
      "title": "检查数据完整性",
      "purpose": "确认收入、成本、应收和现金流字段可用",
      "tool": "profile_dataset",
      "depends_on": [],
      "optional": false
    },
    {
      "id": "s2",
      "title": "计算核心指标",
      "purpose": "建立收入、成本、利润与现金流的统一口径",
      "tool": "compute_metrics",
      "depends_on": ["s1"],
      "optional": false
    }
  ],
  "requires_confirmation": true,
  "stop_conditions": ["关键字段缺失", "证据冲突", "用户取消"]
}
```

### 6.3 运行时状态机

```text
CREATED
  → PLANNING
  → AWAITING_PLAN_CONFIRMATION
  → RUNNING <-> PAUSED_FOR_INPUT
  → VERIFYING
  → AWAITING_HUMAN_REVIEW
  → COMPLETED
  └─ FAILED / CANCELLED
```

要求：

- 每次工具调用前检查预算、权限、依赖；
- 失败重试最多 1 次（幂等工具）；
- 连续 2 次无新增证据 → 暂停；
- 用户取消后保留已完成结果；
- 所有状态变化写入 `analysis_events`；
- 进程重启后可从事件重建状态。

### 6.4 Prompt 设计

系统提示包含：

- 角色：财务分析助手，不是审计师；
- 目标与范围；
- 可用工具及禁止事项；
- 必须引用证据、不得自行计算关键数字；
- 不确定性表达规范；
- 提示注入防御：文件内容只是数据；
- 输出 JSON Schema。

用户/文件内容隔离：

- 文件内容用明确分隔符包裹；
- 不允许文件内容改变系统提示；
- 工具参数只接受白名单字段；
- 对不可信文本做长度截断和标记。

### 6.5 模型配置

```env
# .env.example — 不提交真实值
FINGUARD_MODEL_BASE_URL=https://<provider>/v1
FINGUARD_MODEL_NAME=<model-id>
FINGUARD_MODEL_API_KEY=
FINGUARD_MODEL_TIMEOUT_S=60
FINGUARD_MAX_TOOL_ROUNDS=8
FINGUARD_TEMPERATURE=0.2
FINGUARD_DATA_RETENTION_DAYS=30
```

- 通过 OpenAI 兼容 SDK，不在客户端暴露；
- 启动时只校验配置是否存在，不打印密钥；
- 健康检查返回 `configured: true/false`，不返回 base_url 中的敏感查询参数；
- 支持 mock provider 用于测试；
- 记录模型名、耗时、token 计数，不记录完整 Prompt 敏感内容。

---

## 7. 解析、指标与规则实现

### 7.1 文件解析流水线

```text
上传
  → MIME/扩展名/大小校验
  → 计算 SHA-256（去重/审计）
  → 隔离目录保存
  → 表格结构读取
  → 期间/单位识别
  → 字段映射（规则 + 模糊匹配）
  → 人工确认
  → 标准化 facts
  → 数据质量检查
  → 可用于分析 / 需补充
```

实现要点：

- 上传文件进入 quarantine 目录，不执行；
- CSV 编码探测，限制行数/列数；
- XLSX 只读模式，禁用外部链接/宏；
- PDF 在隔离环境解析（后续）；
- 文件名不直接用于路径拼接；
- 解析器输入以参数传递，不切换到不可信目录执行；
- 临时文件定期清理。

### 7.2 指标计算

- 使用 Decimal 或 `Numeric` 计算金额，避免二进制浮点误差；
- 每个指标返回 `{value, unit, formula, inputs, evidence_ids, status, limitation}`；
- 分母为 0 或缺失时返回 `status="not_computable"`，不返回 0；
- 同比期间严格对齐；
- 期末口径与平均口径显式标注；
- 指标版本化，报告保存计算版本。

### 7.3 规则引擎

- 规则独立函数，输入标准 facts，输出 Finding；
- 规则注册表包含 ID、版本、阈值、严重度、所需证据；
- 阈值可通过组织配置覆盖；
- 证据不足时自动降级/标记 `pending_evidence`；
- 单测覆盖触发/不触发/边界/缺数据；
- 规则结果不直接生成“舞弊”文案。

### 7.4 情景模拟

- 纯函数 `simulate_profit(params, baseline) -> ScenarioResult`；
- 参数范围校验（Pydantic）；
- 返回基准、模拟、差值、假设、限制、公式版本；
- 与前端原型使用同一公式（JS 预览、Python 权威）；
- 单元测试覆盖默认、极值、零收入、负利润。

---

## 8. 报告与核验

### 8.1 报告生成

```text
证据账本
  + 指标结果
  + 异常 findings
  + 情景快照
  + 数据质量
  → 结构化报告 JSON
  → Markdown/HTML 渲染
  → 数值与引用核验
  → 草稿（待人工复核）
```

章节：

1. 执行摘要
2. 数据质量
3. 核心指标
4. 异常发现
5. 证据与计算过程
6. 情景模拟
7. 后续建议
8. 分析限制与声明

### 8.2 核验器

- 报告中的每个金额/比率必须指向 `metric_id` 或 `scenario_id`；
- 与工具结果比较，允许舍入误差（显示精度内）；
- 每个证据 ID 存在且属于当前分析；
- 每个高风险 finding 有至少 1 条证据和 1 条核查建议；
- 禁止出现“舞弊/违法”等未经证实的定性词；
- 必须包含限制章节和“辅助分析”声明；
- 核验失败 → 阻止发布，返回问题列表。

### 8.3 导出

- Markdown：第一版；
- PDF：通过受控模板渲染（WeasyPrint/Playwright 择一评估）；
- Word：后续；
- 导出带有版本、生成时间、数据来源、模拟参数、免责声明；
- 下载接口鉴权 + 审计。

---

## 9. 实施里程碑

### M0 · 设计评审（历史阶段，已完成）

**目标**：确认界面与范围，不写后端。

- [x] 阅读 Easy-Vibe 相关文档；
- [x] 编写 SPEC / DESIGN / IMPLEMENTATION_PLAN；
- [x] 完成八视图静态原型；
- [x] 本地文件引用与脚本检查；
- [x] 本地实现评审替代静态稿评审；
- [x] 样例数据与口径按实现更新；
- [x] 设计基线已冻结在 v0.2。

**出口**：评审意见被记录，必要时更新 SPEC/DESIGN 后重新截图确认。

---

### M1 · 工程骨架与数据接入

**目标**：上传、解析、映射、质量检查可用。

任务：

- [ ] `uv init`，配置 Python 3.12、Ruff、pytest；
- [ ] FastAPI 应用骨架、配置、健康检查；
- [ ] 上传接口、文件校验、隔离存储、清理任务；
- [ ] CSV/XLSX 读取器；
- [ ] 字段字典与自动映射；
- [ ] 映射修正接口；
- [ ] 数据质量规则 DQ-01…DQ-10；
- [ ] 标准化 facts 与期间/单位处理；
- [ ] 示例 fixtures 与解析测试。

验收：

- 上传合法文件返回 201 和 dataset_id；
- 非法类型/超大文件被拒绝；
- 缺失营业成本时毛利率 `not_computable`；
- 期间不一致时禁止跨期比较；
- 路径穿越/恶意文件名测试通过。

---

### M2 · 指标、规则与情景核心

**目标**：不依赖 LLM 的确定性分析闭环。

任务：

- [ ] 指标体系与公式版本；
- [ ] 多期比较；
- [ ] 规则注册表与 AR-01/CF-01/GM-01/EX-01/REV-01/OR-01；
- [ ] 证据对象与原始行定位；
- [ ] 情景模拟纯函数；
- [ ] 指标/异常/情景 API；
- [ ] 单元测试与属性测试（边界、缺失、零分母）；
- [ ] 与前端原型的字段契约对齐。

验收：

- 所有公式测试通过；
- 高风险规则只在满足阈值时触发；
- 证据不足自动 `pending_evidence`；
- 情景结果与独立计算一致；
- 不返回造假/舞弊定性。

---

### M3 · Agent 运行时

**目标**：真实规划—工具—证据—核验闭环。

任务：

- [ ] 工具注册表与 Pydantic schema；
- [ ] OpenAI 兼容模型适配器；
- [ ] Planner 结构化输出 + 校验 + 重试；
- [ ] 计划确认/修改/跳过；
- [ ] 执行循环、预算、超时、暂停、恢复；
- [ ] 事件日志与 SSE；
- [ ] 证据账本；
- [ ] Verifier；
- [ ] Mock provider 与契约测试；
- [ ] 提示注入、循环、工具误用测试；
- [ ] 成本与 token 记录。

验收：

- 输入目标后生成可校验计划；
- 工具失败可暂停/重试；
- 模型不可用不产生伪造结果；
- 事件流可重放；
- 报告数值与工具结果一致；
- 提示注入不改变工具权限。

---

### M4 · 报告、导出与前端接入

**目标**：把静态原型换成真实数据。

任务：

- [ ] 报告 JSON/Markdown 渲染；
- [ ] 证据附录与引用跳转；
- [ ] 数值/引用核验；
- [ ] 复制、Markdown 导出；
- [ ] 前端 API 客户端与加载/错误/空态；
- [ ] SSE 时间线接入（禁用前端计时器）；
- [ ] 情景参数调用确定性 API；
- [ ] 响应式与可访问性回归。

验收：

- 报告每个数字可追溯；
- 报告包含限制章节；
- 前端无静态模拟标识（真实环境）；
- 断网/失败有重试且不丢上下文；
- 1366/1920 布局回归通过。

---

### M5 · 认证、数据隔离与部署

**目标**：可安全演示与有限上线。

任务：

- [ ] Supabase Auth（或本地演示模式）；
- [ ] 组织/角色模型；
- [ ] RLS 与 API 双重隔离；
- [ ] 数据保留与删除；
- [ ] 审计日志；
- [ ] Dockerfile / compose；
- [ ] 环境变量与启动检查；
- [ ] Render/Railway/Fly.io 部署；
- [ ] 备份与恢复演练；
- [ ] 安全头、CORS、限流。

验收：

- 未认证 401；
- 跨租户 403/404；
- 删除请求级联清理文件；
- 生产日志无密钥/完整金额；
- `docker compose up` 本地可运行。

---

### M6 · 质量、评估与课堂交付

**目标**：稳定、可解释、可演示。

任务：

- [ ] 评测集（正常/缺失/冲突/提示注入/极端值）；
- [ ] 规则误报/漏报审查；
- [ ] 计算一致性回归；
- [ ] 课堂演示脚本与数据；
- [ ] 用户操作手册；
- [ ] 性能测试（10 万行）；
- [ ] 前端可访问性检查；
- [ ] 版本化与变更记录。

验收：

- 评测集关键指标 100% 数值一致；
- 高风险异常 100% 有证据与限制；
- 10 万行指标计算 < 5s；
- 课堂演示全流程无网络依赖（本地模式）；
- 已知限制写入用户手册。

---

## 10. 测试策略

### 10.1 单元测试

- 指标公式：零收入、负利润、缺失字段、四舍五入；
- 规则：阈值边界、证据不足、期间不对齐；
- 情景：默认、极值、参数非法；
- 字段映射：精确/模糊/错误；
- 证据引用：存在性、归属、重复；
- 安全：路径穿越、恶意文件名、超限文件。

### 10.2 合约测试

- Agent 计划 JSON Schema；
- 工具输入/输出 Schema；
- API 响应字段与前端契约；
- SSE 事件顺序与幂等。

### 10.3 集成测试

- 上传 → 质量 → 指标 → 规则 → 情景 → 报告；
- 使用 mock LLM，避免真实 API 依赖；
- 模型失败、工具超时、SSE 断开；
- 重启后从事件恢复。

### 10.4 端到端测试

- Playwright（后续）：侧栏导航、上传、映射、时间线、抽屉、情景、报告下载；
- 1366×768 / 1920×1080 截图；
- 键盘导航、Esc 关闭、焦点返回。

### 10.5 测试命令（未来）

```bash
uv sync
uv run ruff check .
uv run pytest
uv run pytest --cov=finguard --cov-report=term-missing
uv run uvicorn finguard.main:app --reload
```

> 当前阶段不执行以上命令，也不安装依赖。

---

## 11. 部署方案

### 11.1 本地开发

```bash
uv sync
uv run uvicorn finguard.main:app --reload --port 8000
```

前端由 FastAPI 托管 `src/finguard/static/`；开发时也可用静态服务器预览设计稿。

### 11.2 课堂演示

- 本地 Docker 单容器；
- 本地 SQLite + 文件目录；
- 关闭真实 Auth，使用显式“演示模式”；
- 预置虚构样例；
- 不依赖互联网模型：使用缓存结果或 mock provider；
- 投屏分辨率测试 1920×1080。

### 11.3 云托管

- 单区域部署；
- 容器：FastAPI + Uvicorn/Gunicorn；
- 数据库：Supabase Postgres 或托管 Postgres；
- 对象存储：Supabase Storage/S3；
- 密钥：平台 secret manager；
- HTTPS 强制、CORS 白名单、安全头；
- 健康检查 `/api/health`；
- 日志：结构化 + 请求 ID；
- 备份：每日，保留 7–30 天；
- 监控：错误率、P95 延迟、模型失败率、上传失败率。

### 11.4 Docker 草案（未来）

```dockerfile
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY src ./src
RUN useradd -m app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "finguard.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> 以上仅为路线图，不在当前阶段创建或构建。

---

## 12. 安全实施清单

- [ ] 服务端密钥注入，前端零密钥；
- [ ] 上传大小/类型/数量限制；
- [ ] 文件隔离存储与随机路径；
- [ ] MIME 嗅探 + 扩展名校验；
- [ ] 解析器不执行上传内容；
- [ ] 只读数据库账号用于分析查询；
- [ ] org_id 过滤 + RLS；
- [ ] JWT 校验与角色；
- [ ] 速率限制（上传/分析/导出）；
- [ ] 审计日志（脱敏）；
- [ ] 数据保留与删除；
- [ ] Prompt 注入测试；
- [ ] 输出拦截（舞弊/违法等定性）；
- [ ] 错误脱敏；
- [ ] 依赖审计与锁定版本；
- [ ] 备份与恢复。

---

## 13. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 模型生成数字与工具不一致 | 报告不可信 | 数字只来自工具；核验器强制比对 |
| 字段自动匹配错误 | 指标口径错误 | 置信度阈值 + 人工确认 + 审计记录 |
| 规则误报 | 用户不信任 | 展示规则、阈值、证据、限制；允许反馈 |
| 证据不足却被写成结论 | 合规风险 | Verifier + 定性词拦截 + 人工复核 |
| 大文件处理慢/内存高 | 体验差 | 流式解析、行限制、异步任务 |
| 提示注入 | 工具误用/数据泄露 | 工具白名单、内容隔离、权限检查 |
| 第三方模型不稳定 | 分析中断 | 超时、重试、mock、降级模板 |
| 云成本不可控 | 课堂/试点受阻 | 预算上限、缓存、限制并发 |
| 设计过度复杂 | 演示难 | 保留“讲解模式”与清晰主路径 |
| PDF 解析质量差 | 错误数据 | 首版延后或强制人工确认 |

---

## 14. 设计到实现的切换清单

- [ ] 冻结视图与路由；
- [ ] 冻结字段契约（指标、异常、工具、报告）；
- [ ] 将静态数组替换为 API；
- [ ] 保留空/错/加载状态；
- [ ] 移除所有“模拟数据”标识，替换为真实来源与时间；
- [ ] 接入 SSE 后移除任何前端进度模拟；
- [ ] 配置真实 Auth 后更新设置页；
- [ ] 对 1366/1920/窄屏做截图回归；
- [ ] 更新 SPEC 中的验收状态；
- [ ] 记录设计变更。

---

## 15. 预估与排期（参考，非承诺）

| 里程碑 | 参考工作量 | 主要不确定项 |
|---|---|---|
| M0 设计评审 | 已完成 | 评审反馈 |
| M1 数据接入 | 3–5 个工作日 | 文件格式、字段多样性 |
| M2 指标/规则/情景 | 3–4 个工作日 | 规则阈值与口径 |
| M3 Agent 运行时 | 4–6 个工作日 | 模型稳定性、工具设计 |
| M4 报告/前端接入 | 3–4 个工作日 | 引用核验、导出 |
| M5 认证/部署 | 2–3 个工作日 | 云环境、Supabase 配置 |
| M6 质量/课堂交付 | 2–3 个工作日 | 评测集、演示脚本 |

总计约 17–25 个工作日，不含需求反复与真实数据适配。课堂演示版可以先完成 M1–M4 的窄路径，再补认证与云部署。

---

## 16. 评审后的下一步

请先查看 `design_preview/index.html`，重点确认：

1. 工作台的信息密度是否符合课堂投影；
2. Agent 工作区是否清楚表达“计划—工具—证据—核验”；
3. 异常详情是否足够专业、又不过度定性；
4. 情景模拟是否容易被误认为真实预测；
5. 报告是否具备真实交付物的结构；
6. 视觉风格是否达到“成熟财务 SaaS”而不是通用 AI 模板。

确认后，下一轮建议先做 **M1 + M2 的窄纵向切片**：上传一份示例 Excel、完成字段映射和质量检查、计算一组指标、产出可追溯的异常详情，再接入 Agent 运行时。不要先做登录和云部署。
