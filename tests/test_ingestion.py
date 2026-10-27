from __future__ import annotations

from pathlib import Path

from finguard.demo import parse_sample_upload
from finguard.ingestion.parser import apply_item_overrides, build_file_meta, parse_files


def _write_csv(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_missing_value_is_not_converted_to_zero(tmp_path, temp_settings):
    path = tmp_path / "利润表_缺失.csv"
    _write_csv(
        path,
        "statement,period,item,value,unit\n"
        "income,2025-9M,营业收入,1000000,元\n"
        "income,2025-9M,营业成本,,元\n",
    )
    meta = build_file_meta(path, path.name, "text/csv")
    dataset = parse_files([(path, meta)], dataset_id="test", company="测试公司")
    cogs = [fact for fact in dataset.facts if fact.raw_item == "营业成本"][0]
    assert cogs.status == "missing"
    assert cogs.value is None
    assert any(issue.code == "DQ-MISSING-VALUE" for issue in dataset.quality)
    assert any(issue.blocking for issue in dataset.quality)


def test_temporary_receipts_are_not_auto_mapped_or_zeroed(temp_settings):
    dataset = parse_sample_upload(temp_settings)
    temporary = [fact for fact in dataset.facts if fact.raw_item == "暂收款"]
    assert len(temporary) == 1
    assert temporary[0].field is None
    assert temporary[0].status == "unmapped"
    assert any(issue.code == "DQ-UNMAPPED-ITEM" for issue in dataset.quality)
    assert temporary[0].value is not None  # original value preserved for review
    # It must not become 0 or be silently mapped to other receivables.
    assert temporary[0].field != "other_receivables"


def test_mapping_override_requires_explicit_field(temp_settings):
    dataset = parse_sample_upload(temp_settings)
    dataset = apply_item_overrides(dataset, {"暂收款": "other_receivables"})
    temporary = [fact for fact in dataset.facts if fact.raw_item == "暂收款"][0]
    assert temporary.field == "other_receivables"
    assert temporary.status == "valid"

    dataset = apply_item_overrides(dataset, {"暂收款": None})
    temporary = [fact for fact in dataset.facts if fact.raw_item == "暂收款"][0]
    assert temporary.field is None
    assert temporary.status == "unmapped"
    item = [item for item in dataset.mapping.items if item.raw_item == "暂收款"][0]
    assert item.status == "confirmed_unmapped"


def test_wide_format_is_supported(tmp_path):
    path = tmp_path / "利润表_宽表.csv"
    _write_csv(path, "科目,2024 前三季度,2025 前三季度\n营业收入,100,150\n营业成本,60,90\n")
    meta = build_file_meta(path, path.name, "text/csv")
    dataset = parse_files([(path, meta)], dataset_id="wide", company="宽表公司")
    revenue_2025 = [fact for fact in dataset.facts if fact.field == "revenue" and fact.period_raw == "2025 前三季度"]
    assert len(revenue_2025) == 1
    assert int(revenue_2025[0].value) == 150
    assert dataset.mapping.layout == "wide"


def test_basic_text_pdf_is_parsed(tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "利润表_2025.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(60, 800, "Income statement")
    pdf.drawString(60, 780, "Period: 2025-9M")
    pdf.drawString(60, 760, "Revenue 128,400,000")
    pdf.drawString(60, 740, "Operating Cash Flow 9,860,000")
    pdf.drawString(60, 720, "Accounts Receivable 38,420,000")
    pdf.save()

    meta = build_file_meta(path, path.name, "application/pdf")
    dataset = parse_files([(path, meta)], dataset_id="pdf", company="PDF 公司")
    fields = {fact.field for fact in dataset.facts}
    assert "revenue" in fields
    assert "operating_cash_flow" in fields
    assert "accounts_receivable" in fields
    assert dataset.mapping.layout == "pdf_text"
