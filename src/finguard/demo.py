from __future__ import annotations

import io
import uuid
from pathlib import Path

import pandas as pd

from finguard.config import Settings
from finguard.ingestion.parser import build_file_meta, parse_files
from finguard.models import Dataset

DEMO_COMPANY = "澄海智造股份有限公司"
DEMO_GOAL = "评估 2025 年前三季度收入增长与利润、现金流变化之间的关系，识别可核查的异常，并说明证据边界。"
SAMPLE_COMPANY = "星禾商贸有限公司"
SAMPLE_FILE_NAME = "星禾商贸_示例上传.xlsx"


def build_demo_dataset(settings: Settings) -> Dataset:
    specs = []
    for path in sorted(settings.demo_dir.glob("*.csv")):
        meta = build_file_meta(path, path.name, "text/csv")
        specs.append((path, meta))
    if not specs:
        raise FileNotFoundError("缺少 data/demo 演示 CSV 文件")
    return parse_files(
        specs,
        dataset_id="demo-chl",
        company=DEMO_COMPANY,
        is_demo=True,
        source_label="演示数据 · 4 个本地 CSV",
    )


def _sample_frames() -> dict[str, pd.DataFrame]:
    income_rows = [
        ("income", "2025-9M", "营业收入", 64200000, "元"),
        ("income", "2025-9M", "营业成本", 47300000, "元"),
        ("income", "2025-9M", "销售费用", 7800000, "元"),
        ("income", "2025-9M", "管理费用", 4200000, "元"),
        ("income", "2025-9M", "研发费用", 1900000, "元"),
        ("income", "2025-9M", "财务费用", 600000, "元"),
        ("income", "2025-9M", "其他净收益", 900000, "元"),
        ("income", "2025-9M", "所得税费用", 825000, "元"),
        ("income", "2025-9M", "净利润", 2475000, "元"),
        ("income", "2024-9M", "营业收入", 58900000, "元"),
        ("income", "2024-9M", "营业成本", 41500000, "元"),
        ("income", "2024-9M", "销售费用", 6600000, "元"),
        ("income", "2024-9M", "管理费用", 3900000, "元"),
        ("income", "2024-9M", "研发费用", 1600000, "元"),
        ("income", "2024-9M", "财务费用", 500000, "元"),
        ("income", "2024-9M", "其他净收益", 600000, "元"),
        ("income", "2024-9M", "所得税费用", 1350000, "元"),
        ("income", "2024-9M", "净利润", 4050000, "元"),
        ("income", "2025-07", "营业收入", 8200000, "元"),
        ("income", "2025-08", "营业收入", 8900000, "元"),
        ("income", "2025-09", "营业收入", 14400000, "元"),
        ("income", "2024-07", "营业收入", 7900000, "元"),
        ("income", "2024-08", "营业收入", 8100000, "元"),
        ("income", "2024-09", "营业收入", 9300000, "元"),
    ]
    balance_rows = [
        ("balance", "2025-09-30", "货币资金", 5200000, "元"),
        ("balance", "2025-09-30", "应收账款", 21800000, "元"),
        ("balance", "2025-09-30", "其他应收款", 4200000, "元"),
        ("balance", "2025-09-30", "存货", 12400000, "元"),
        ("balance", "2025-09-30", "应付账款", 9700000, "元"),
        ("balance", "2025-09-30", "暂收款", 1200000, "元"),
        ("balance", "2025-09-30", "总资产", 60000000, "元"),
        ("balance", "2025-09-30", "总负债", 35000000, "元"),
        ("balance", "2025-09-30", "所有者权益", 25000000, "元"),
        ("balance", "2024-09-30", "货币资金", 4800000, "元"),
        ("balance", "2024-09-30", "应收账款", 14300000, "元"),
        ("balance", "2024-09-30", "其他应收款", 1900000, "元"),
        ("balance", "2024-09-30", "存货", 10800000, "元"),
        ("balance", "2024-09-30", "应付账款", 9100000, "元"),
        ("balance", "2024-09-30", "总资产", 49000000, "元"),
        ("balance", "2024-09-30", "总负债", 29000000, "元"),
        ("balance", "2024-09-30", "所有者权益", 20000000, "元"),
    ]
    cash_rows = [
        ("cashflow", "2025-9M", "经营活动产生的现金流量净额", 2400000, "元"),
        ("cashflow", "2025-9M", "投资活动产生的现金流量净额", -3100000, "元"),
        ("cashflow", "2025-9M", "筹资活动产生的现金流量净额", 1200000, "元"),
        ("cashflow", "2025-9M", "现金及现金等价物净增加额", 500000, "元"),
        ("cashflow", "2025-9M", "期末现金及现金等价物余额", 5200000, "元"),
        ("cashflow", "2024-9M", "经营活动产生的现金流量净额", 4100000, "元"),
        ("cashflow", "2024-9M", "投资活动产生的现金流量净额", -2000000, "元"),
        ("cashflow", "2024-9M", "筹资活动产生的现金流量净额", 200000, "元"),
        ("cashflow", "2024-9M", "现金及现金等价物净增加额", 2300000, "元"),
        ("cashflow", "2024-9M", "期末现金及现金等价物余额", 4800000, "元"),
    ]
    expense_rows = [
        ("expense_detail", "2025-9M", "推广服务费", 1800000, "元"),
        ("expense_detail", "2025-9M", "职工薪酬", 3700000, "元"),
        ("expense_detail", "2025-9M", "差旅费", 900000, "元"),
        ("expense_detail", "2025-9M", "运输费", 700000, "元"),
        ("expense_detail", "2025-9M", "其他费用", 700000, "元"),
        ("expense_detail", "2024-9M", "推广服务费", 1500000, "元"),
        ("expense_detail", "2024-9M", "职工薪酬", 3200000, "元"),
        ("expense_detail", "2024-9M", "差旅费", 800000, "元"),
        ("expense_detail", "2024-9M", "运输费", 600000, "元"),
        ("expense_detail", "2024-9M", "其他费用", 500000, "元"),
    ]
    return {
        "利润表": pd.DataFrame(income_rows, columns=["statement", "period", "item", "value", "unit"]),
        "资产负债表": pd.DataFrame(balance_rows, columns=["statement", "period", "item", "value", "unit"]),
        "现金流量表": pd.DataFrame(cash_rows, columns=["statement", "period", "item", "value", "unit"]),
        "费用明细": pd.DataFrame(expense_rows, columns=["statement", "period", "item", "value", "unit"]),
    }


def ensure_sample_upload(settings: Settings) -> Path:
    """Create a real XLSX that a reviewer can download and upload again."""
    settings.demo_dir.mkdir(parents=True, exist_ok=True)
    path = settings.demo_dir / SAMPLE_FILE_NAME
    if path.exists():
        return path
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet, frame in _sample_frames().items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
    path.write_bytes(buffer.getvalue())
    return path


def parse_sample_upload(settings: Settings) -> Dataset:
    path = ensure_sample_upload(settings)
    meta = build_file_meta(path, path.name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    dataset = parse_files(
        [(path, meta)],
        dataset_id=f"sample-{uuid.uuid4().hex[:8]}",
        company=SAMPLE_COMPANY,
        is_demo=False,
        source_label="上传示例 · XLSX（含模糊字段“暂收款”）",
    )
    return dataset
