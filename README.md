# FinGuard

FinGuard 是一个面向财务分析教学场景的 **AI Agent 财务诊断工作台**，不是聊天机器人，也不是只有视觉效果的静态页面。

它完成一条可运行、可复核的闭环：

**导入财务数据 → 数据校验 → Agent 制定计划 → 调用确定性工具 → 计算指标 → 检查规则 → 展示证据 → 证据不足时暂停 → 人工确认限制 → 生成 Markdown/HTML 报告。**

> 演示公司「澄海智造股份有限公司」与「星禾商贸有限公司」均为虚构主体，数据仅用于教学演示。FinGuard 不替代审计、鉴证、税务或投资判断。

---

## 1. 已实现能力

### 数据接入
- 上传 CSV、XLSX 和基础文本型 PDF（本机 FastAPI 后端解析）。
- 长表（`报表/期间/科目/金额/单位`）和带期间列的宽表。
- 手工确认字段映射；“暂收款”等含义不明确的科目 **不会自动映射**。
- 缺失值、无效数值、单位、期间、重复记录、资产负债表勾稽检查。
- 空白值保持为空，不补 0；每个事实保留文件、工作表、行号、期间与原始值。
- 内置第二套 XLSX 示例「星禾商贸」：可下载后上传，也可一键载入。

### 确定性财务分析
- 营业收入、营业成本、毛利率、净利润、净利率。
- 经营现金流、OCF/净利润、销售费用率。
- 应收账款增速与周转天数、其他应收款占比。
- 营运资本净占用（应收 + 其他应收 + 存货 − 应付）。
- 季末月收入占季度比重。
- 缺数据时显示“不可计算”，不用 0 代替。

### Agent 运行时
- 8 个真实执行步骤：校验 → 指标 → 收入利润 → 现金流 → 规则 → 证据核验 → 报告 → 报告核验。
- 每一步记录真实工具名、输入参数、返回摘要、耗时、状态和证据 ID。
- 指标、规则、证据与报告均由 Python 生成，不由模型心算。
- 连续两期条件不足时，AR-01 / CF-01 自动降级为“候选 · 待核验”，不冒充已触发。
- 证据不足时 Agent 真实暂停；用户可确认限制并继续，或补充/重新导入数据。
- 支持单步执行、运行到暂停、确认并继续、重新执行。

### 报告
- 由结构化分析状态生成 Markdown 和 HTML。
- 页面显示与下载文件使用同一份结果。
- 包含企业/数据范围、质量检查、指标、规则、原始证据、公式、情景模拟、Agent 执行记录、待核验问题、限制。
- Markdown 下载来自后端；PDF 通过浏览器打印生成。

### 课堂模式
- **投影模式**：放大标题、正文、指标和表格，适配 1920×1080 投屏。
- **讲解模式**：计划 → 工具 → 证据 → 暂停核验 → 结论，支持上一步、下一步、自动演示和退出，全部读取当前真实执行记录。

### 模型
- 默认 **完全本地运行**，不调用远程模型。
- 可通过服务端环境变量接入 OpenAI 兼容的 Chat Completions 服务。
- 支持报告叙述、按需「AI 解读」和用户主动触发的模型连接测试。
- 所有关键数字由程序计算并核验；模型输出禁止引入数字或舞弊/违法定性。
- 远程调用失败、超时或输出未经验证的数字时，自动回退到本地叙述。
- API Key 只存在于服务端环境变量，前端无法读取。

---

## 2. 快速开始

### 环境要求
- Ubuntu / Linux
- Python 3.12
- [uv](https://docs.astral.sh/uv/)

### 安装并启动

```bash
cd /home/chen/projects/finguard
uv sync
uv run finguard
```

打开 <http://127.0.0.1:8000>。

启动时应用会自动：
1. 解析 `data/demo/` 中的澄海智造演示 CSV；
2. 创建一条真实分析任务；
3. 执行前 5 个工具步骤；
4. 在“证据核验”处真实暂停，等待你确认证据边界。

点击 Agent 页面的「确认限制并继续」，会继续执行报告生成与报告核验。也可以点击「载入演示分析」重新开始。

> `uv run uvicorn finguard.main:app --reload --port 8000` 也可用于开发调试。

---

## 3. 数据导入

### 推荐长表格式

CSV 或 XLSX 列：

```csv
statement,period,item,value,unit,note
income,2025-9M,营业收入,128400000,元,
income,2025-9M,营业成本,88080000,元,
balance,2025-09-30,应收账款,38420000,元,
cashflow,2025-9M,经营活动产生的现金流量净额,9860000,元,
```

支持列名别名（如 `报表/期间/科目/金额/单位`、`statement/period/item/value/unit`）。也支持宽表：

```csv
科目,2024 前三季度,2025 前三季度
营业收入,118230000,128400000
营业成本,77930000,88080000
```

### 示例文件

在页面点击「下载示例 XLSX」，或直接访问：

```bash
curl -OJ http://127.0.0.1:8000/api/demo/sample.xlsx
```

该文件包含利润表、资产负债表、现金流量表、费用明细四个工作表，以及一个故意不自动映射的「暂收款」字段。

### PDF
支持带可提取文本的简单报表。扫描件、图片型 PDF、复杂跨页表格需要 OCR 或人工整理；解析失败会明确显示错误，不会伪造数据。

---

## 4. 模型配置（可选）

```bash
cp .env.example .env
```

填写：

```env
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your-server-side-key
LLM_MODEL=your-model-name
LLM_TIMEOUT_SECONDS=30
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=1200
# Optional when the provider does not use /v1/chat/completions:
# LLM_COMPLETIONS_PATH=/v3/chat/completions
```

也支持 `OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL` 和 `DEEPSEEK_*` 兼容别名；`LLM_*` 优先。

### 当前 LLM 功能

1. **报告叙述**：Agent 生成报告时会调用远程模型生成定性叙述；数字仍由 Python 计算。
2. **AI 解读**：Agent 工作区可按需生成结构化解读，输出 `summary / signals / questions / limitations`。
3. **模型连接测试**：设置页的「测试模型」会发送一个不含财务数据的 `OK` 测试请求，并显示延迟或失败原因。

### 边界与安全

- `LLM_BASE_URL` 应指向 OpenAI 兼容 Chat Completions API；程序会补齐 `/v1/chat/completions`（若 Base URL 尚未包含版本段）。
- Volcengine Ark Coding 类网关会自动使用 `/v3/chat/completions`；如路径不同，设置 `LLM_COMPLETIONS_PATH`。
- 密钥只在服务端读取，不进入 HTML/JavaScript 或 API 响应。
- 远程模型发送范围：指标方向、规则状态、限制与待核验事项；**不发送原始文件全文或逐行明细**。
- 模型输出会做结构化校验：包含未经验证的数字、舞弊/违法定性或非法 JSON 时会被拒绝。
- 未配置、超时、HTTP 错误或输出校验失败时：自动回退到本地确定性叙述；财务计算和报告不受影响。
- 429 会显示“模型限流或配额不足”；401/403、404、5xx 分别提示认证、路径和服务问题。
- 默认不信任 shell/system 代理；如模型服务必须走代理，设置 `LLM_TRUST_ENV=1`。
- 系统不会把“已配置”伪装成“已连接”。设置页的「测试模型」是用户主动触发的真实请求。

---

## 5. 测试

由于部分 Ubuntu + ROS 环境通过 `PYTHONPATH` 注入了不兼容的 pytest 插件，项目提供专用测试入口：

```bash
./scripts/test.sh
```

等价于：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest
```

测试覆盖：
- 正常财务数据与关键公式
- A-05 季末月占比重算（63.7%，+27.5pp）
- 连续两期条件不足时降级为候选
- 不满足规则时不生成用户可见异常
- 缺失值不补 0
- “暂收款”不自动映射
- 宽表与基础 PDF 解析
- 上传 XLSX → 人工映射 → 创建分析 → 暂停
- 数据不足时阻塞
- Agent 暂停、确认限制、报告生成与核验
- 情景模拟基准与透明公式
- 报告 Markdown 与页面结果一致
- 未配置模型时的本地运行
- 配置模型失败时的安全回退
- AI 解读 JSON 结构校验、数字/定性拦截
- 模型连接测试不发送财务数据
- API 响应不泄露密钥

最近一次自动化测试：`29 passed`（包含 LLM 校验、连接测试与端点解析），另有 1 个来自 FastAPI/Starlette 测试客户端的弃用警告。

---

## 6. 一键部署

### Docker Compose（推荐本地/课堂）

```bash
docker compose up --build
```

打开 <http://127.0.0.1:8000>。Docker 使用命名卷 `finguard_uploads`、`finguard_runtime` 保存上传文件与运行状态，避免宿主机用户与容器用户权限不一致。

### Render / 云容器

- `Dockerfile` 可直接用于 Render、Railway、Fly.io 等容器平台。
- 提供 `render.yaml` 作为 Render Blueprint 起点。
- 云平台需要配置环境变量；未配置 `LLM_*` 时仍可用本地确定性模式。
- 免费实例通常不保证持久磁盘；正式使用请挂载持久卷或使用外部存储。

---

## 7. 本地数据与安全边界

- 上传文件保存在 `data/uploads/`。
- 运行状态保存在 `data/runtime/store.json`。
- 删除这两个目录即可清理本地数据（建议停止服务后操作）。
- 当前为单用户本地演示模式，**不是生产级认证或多租户系统**。
- 未接入 Supabase Auth、云数据库或对象存储。
- 没有远端审计日志、多用户权限、数据保留策略 UI。
- 如果启用远程模型，结构化财务摘要会离开本机；设置页会明确披露发送范围。
- 不应把真实敏感财务数据上传到未经审查的第三方模型。

---

## 8. 项目结构

```text
finguard/
├── src/finguard/
│   ├── main.py                  # FastAPI 应用、API、静态站点
│   ├── config.py                # 环境变量与路径
│   ├── models.py                # Pydantic 数据模型
│   ├── store.py                 # 本地数据集/分析状态存储
│   ├── demo.py                  # 澄海智造与星禾商贸示例
│   ├── llm.py                   # 可选远程叙述客户端
│   ├── ingestion/               # CSV/XLSX/PDF 解析、字段映射、质量
│   ├── analysis/                # 指标、规则、情景、报告
│   └── agent/runtime.py         # 真实步骤执行与暂停/恢复
├── design_preview/              # 前端（由 FastAPI 托管）
├── data/demo/                   # 演示 CSV 与生成的 XLSX 示例
├── tests/                       # 29 项自动化测试
├── docs/                        # SPEC / DESIGN / IMPLEMENTATION_PLAN
├── Dockerfile
├── compose.yaml
├── render.yaml
├── .env.example
└── scripts/test.sh
```

---

## 9. 当前未完成 / 外部依赖

- **Supabase Auth / 生产级登录**：未接入，本地模式无需登录。
- **云数据库 / 多租户**：未接入；当前本地单用户。
- **扫描 PDF OCR**：未实现；仅支持可提取文本的 PDF。
- **服务端 PDF 导出**：未引入 WeasyPrint；当前使用浏览器打印为 PDF。
- **远程模型成功连接**：需要你自己提供 `LLM_*` 配置并实际测试；系统不会在没有调用的情况下声称已连接。
- **正式部署**：提供 Docker/Render 配置，但未替你创建云账号或部署实例。

---

## 10. 文档

- `docs/SPEC.md` — 产品需求与验收标准
- `docs/DESIGN.md` — 界面与交互设计
- `docs/IMPLEMENTATION_PLAN.md` — 架构与实现路线
