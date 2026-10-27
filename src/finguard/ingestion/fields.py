from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CANONICAL_LABELS: dict[str, str] = {
    "revenue": "营业收入",
    "cogs": "营业成本",
    "selling_expenses": "销售费用",
    "admin_expenses": "管理费用",
    "rd_expenses": "研发费用",
    "finance_expenses": "财务费用",
    "other_income_net": "其他净收益",
    "income_tax": "所得税费用",
    "net_profit": "净利润",
    "accounts_receivable": "应收账款",
    "other_receivables": "其他应收款",
    "inventory": "存货",
    "accounts_payable": "应付账款",
    "cash": "货币资金",
    "total_assets": "总资产",
    "total_liabilities": "总负债",
    "equity": "所有者权益",
    "operating_cash_flow": "经营活动现金流量净额",
    "investing_cash_flow": "投资活动现金流量净额",
    "financing_cash_flow": "筹资活动现金流量净额",
    "net_cash_change": "现金及现金等价物净增加额",
    "cash_end": "期末现金及现金等价物余额",
    "expense_promotion": "推广服务费",
    "expense_staff": "销售职工薪酬",
    "expense_travel": "差旅费",
    "expense_transport": "运输费",
    "expense_other": "其他销售费用",
}

# Deliberately keep ambiguous labels out of this dictionary. "暂收款" is a good
# example: it can be a liability, a receipt in transit or a receivable offset,
# so it must be confirmed by a human rather than mapped automatically.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": ("营业收入", "营业总收入", "主营业务收入", "营收", "revenue", "operating revenue"),
    "cogs": ("营业成本", "主营业务成本", "营业总成本", "cost of sales", "cogs"),
    "selling_expenses": ("销售费用", "营业费用", "销售及营销费用", "selling expenses"),
    "admin_expenses": ("管理费用", "administrative expenses", "g&a"),
    "rd_expenses": ("研发费用", "研发支出", "r&d expenses", "research and development"),
    "finance_expenses": ("财务费用", "finance expenses", "finance costs"),
    "other_income_net": ("其他净收益", "其他收益", "营业外收支净额", "其他收入净额", "other income"),
    "income_tax": ("所得税费用", "所得税", "income tax"),
    "net_profit": ("净利润", "归属于母公司股东的净利润", "归母净利润", "net profit", "net income"),
    "accounts_receivable": ("应收账款", "应收款项", "accounts receivable", "receivables"),
    "other_receivables": ("其他应收款", "其它应收款", "other receivables"),
    "inventory": ("存货", "库存", "inventory"),
    "accounts_payable": ("应付账款", "应付款项", "accounts payable", "payables"),
    "cash": ("货币资金", "现金及银行存款", "cash and cash equivalents"),
    "total_assets": ("总资产", "资产总计", "total assets"),
    "total_liabilities": ("总负债", "负债合计", "total liabilities"),
    "equity": ("所有者权益", "股东权益", "净资产", "total equity", "equity"),
    "operating_cash_flow": (
        "经营活动产生的现金流量净额",
        "经营活动现金流量净额",
        "经营活动现金流净额",
        "经营活动现金流",
        "operating cash flow",
        "net cash from operating activities",
    ),
    "investing_cash_flow": (
        "投资活动产生的现金流量净额",
        "投资活动现金流量净额",
        "投资活动现金流",
        "investing cash flow",
    ),
    "financing_cash_flow": (
        "筹资活动产生的现金流量净额",
        "筹资活动现金流量净额",
        "筹资活动现金流",
        "financing cash flow",
    ),
    "net_cash_change": (
        "现金及现金等价物净增加额",
        "现金净增加额",
        "net increase in cash",
    ),
    "cash_end": (
        "期末现金及现金等价物余额",
        "期末现金及现金等价物",
        "期末现金余额",
        "cash at end of period",
    ),
    "expense_promotion": ("推广服务费", "市场推广费", "推广费"),
    "expense_staff": ("职工薪酬", "销售职工薪酬", "销售人员薪酬"),
    "expense_travel": ("差旅费", "交通差旅费"),
    "expense_transport": ("运输费", "物流费", "运费"),
    "expense_other": ("其他费用", "其他销售费用", "销售其他费用"),
}

AMBIGUOUS_ITEMS: dict[str, str] = {
    "暂收款": "可能是预收款、其他应付款或往来过渡科目，未自动映射。",
    "往来款": "对手方与业务性质不明，未自动映射。",
    "其他": "过于宽泛，需结合报表和科目明细确认。",
}

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "statement": ("statement", "statement_type", "报表", "报表类型", "表名", "类型"),
    "period": ("period", "期间", "时间", "日期", "会计期间", "年度", "月份"),
    "item": ("item", "account", "科目", "项目", "字段", "名称", "会计科目"),
    "value": ("value", "amount", "金额", "数值", "本期金额", "发生额", "余额"),
    "unit": ("unit", "单位", "币种", "currency"),
    "note": ("note", "备注", "说明", "备注信息"),
}

STATEMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "income": ("利润表", "损益表", "income", "profit", "income statement"),
    "balance": ("资产负债表", "balance", "balance sheet"),
    "cashflow": ("现金流量表", "现金流", "cashflow", "cash flow"),
    "expense_detail": ("费用明细", "销售费用明细", "expense", "费用"),
}

PERIOD_COLUMN_PATTERN = re.compile(
    r"(20\d{2}.*(?:9M|前三季度|1[-—至]9月|Q[1-4]|第[一二三四]季度|年|\d{1,2}月|期末)|\d{4}[-/]\d{1,2}[-/]\d{1,2})",
    re.IGNORECASE,
)


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("：", ":").replace("（", "(").replace("）", ")")
    text = re.sub(r"[\s ]+", "", text)
    text = text.replace("其中:", "")
    return text.lower()


def match_field(raw_item: str) -> tuple[str | None, float, str]:
    normalized = normalize_text(raw_item)
    if not normalized:
        return None, 0.0, "unmapped"
    if normalized in AMBIGUOUS_ITEMS:
        return None, 0.0, "ambiguous"
    for field, aliases in FIELD_ALIASES.items():
        if normalized in {normalize_text(alias) for alias in aliases}:
            return field, 1.0, "mapped"
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            ah = normalize_text(alias)
            if len(ah) >= 3 and ah in normalized:
                return field, 0.82, "fuzzy"
    return None, 0.0, "unmapped"


def match_statement(hint: str) -> str:
    normalized = normalize_text(hint)
    for statement, aliases in STATEMENT_ALIASES.items():
        if any(normalize_text(alias) in normalized for alias in aliases):
            return statement
    return "unknown"


def match_column(header: str) -> tuple[str | None, float]:
    normalized = normalize_text(header)
    for role, aliases in COLUMN_ALIASES.items():
        if normalized in {normalize_text(alias) for alias in aliases}:
            return role, 1.0
    for role, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if normalize_text(alias) in normalized:
                return role, 0.8
    return None, 0.0


@dataclass(frozen=True, slots=True)
class ParsedValue:
    value: object | None
    status: str
    message: str = ""
