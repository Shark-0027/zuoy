from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
from pypdf import PdfReader

from finguard.ingestion.fields import (
    CANONICAL_LABELS,
    PERIOD_COLUMN_PATTERN,
    match_column,
    match_field,
    match_statement,
    normalize_text,
)
from finguard.ingestion.periods import parse_period
from finguard.ingestion.quality import build_quality_issues
from finguard.models import (
    Dataset,
    Fact,
    FileMeta,
    MappingColumn,
    MappingInfo,
    MappingItem,
    PeriodInfo,
    RawPreview,
)

MAX_ROWS_PER_SHEET = 50000
MAX_PREVIEW_ROWS = 8


def _fact_id(source_file: str, sheet: str, row: int, item: str, period: str) -> str:
    digest = hashlib.sha1(f"{source_file}|{sheet}|{row}|{item}|{period}".encode()).hexdigest()[:12]
    return f"F-{digest.upper()}"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_file_meta(path: Path, original_name: str, content_type: str = "") -> FileMeta:
    return FileMeta(
        name=original_name,
        stored_name=path.name,
        size_bytes=path.stat().st_size,
        sha256=_file_digest(path),
        content_type=content_type,
    )


def _read_csv(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return pd.read_csv(
                path,
                dtype=str,
                keep_default_na=False,
                encoding=encoding,
                nrows=MAX_ROWS_PER_SHEET,
            )
        except UnicodeDecodeError as exc:
            last_error = exc
        except pd.errors.ParserError as exc:
            last_error = exc
    raise ValueError(f"CSV 解析失败：{last_error}")


def _read_xlsx(path: Path) -> dict[str, pd.DataFrame]:
    sheets = pd.read_excel(
        path,
        sheet_name=None,
        dtype=str,
        keep_default_na=False,
        engine="openpyxl",
    )
    return {
        str(name): frame.head(MAX_ROWS_PER_SHEET).fillna("")
        for name, frame in sheets.items()
    }


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _parse_number(raw: str) -> tuple[Decimal | None, str, str]:
    text = _clean_cell(raw)
    if text == "":
        return None, "missing", "空值不被当作 0"
    if text in {"-", "—", "–", "N/A", "n/a", "NA", "不适用"}:
        return None, "missing", "原值为短横线/不适用，未自动视为 0"

    normalized = text.replace(",", "").replace("，", "").replace(" ", "").replace(" ", "")
    scale = Decimal(1)
    embedded_unit = ""
    if normalized.endswith("万元"):
        normalized = normalized[:-2]
        embedded_unit = "万元"
        scale = Decimal(10000)
    elif normalized.endswith("千元"):
        normalized = normalized[:-2]
        embedded_unit = "千元"
        scale = Decimal(1000)
    elif normalized.endswith("元"):
        normalized = normalized[:-1]
        embedded_unit = "元"

    negative = False
    if normalized.startswith("(") and normalized.endswith(")"):
        negative = True
        normalized = normalized[1:-1]
    if normalized.startswith("（") and normalized.endswith("）"):
        negative = True
        normalized = normalized[1:-1]
    normalized = normalized.replace("−", "-").replace("—", "-")
    if normalized.startswith("-"):
        negative = True
        normalized = normalized[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        return None, "invalid", f"无法解析金额“{text}”"
    try:
        value = Decimal(normalized) * scale
        if negative:
            value = -value
        return value, "valid", embedded_unit
    except InvalidOperation:
        return None, "invalid", f"无法解析金额“{text}”"


def _normalize_unit(raw_unit: str, embedded_unit: str = "") -> tuple[str, Decimal | None, str]:
    unit = (embedded_unit or raw_unit or "").strip()
    if unit in {"元", "CNY", "RMB", "人民币", "人民币元"}:
        return "CNY", Decimal(1), ""
    if unit in {"万元", "万"}:
        return "CNY", Decimal(10000), ""
    if unit in {"千元", "千"}:
        return "CNY", Decimal(1000), ""
    if unit == "":
        return "UNKNOWN", None, "单位缺失，未自动按元处理"
    return unit, None, f"无法识别单位“{unit}”"


def _statement_from_value(raw: str, fallback: str) -> str:
    matched = match_statement(raw)
    if matched != "unknown":
        return matched
    raw_normalized = normalize_text(raw)
    aliases = {
        "income": ("income", "利润"),
        "balance": ("balance", "资产"),
        "cashflow": ("cash", "现金流"),
        "expense_detail": ("expense", "费用"),
    }
    for statement, words in aliases.items():
        if any(word in raw_normalized for word in words):
            return statement
    return fallback


def _headers(frame: pd.DataFrame) -> list[str]:
    return [str(column).strip() for column in frame.columns]


def _preview(file_name: str, sheet: str, frame: pd.DataFrame) -> RawPreview:
    headers = _headers(frame)
    rows = []
    for _, row in frame.head(MAX_PREVIEW_ROWS).iterrows():
        rows.append([_clean_cell(row.get(header, "")) for header in headers])
    return RawPreview(
        file=file_name,
        sheet=sheet,
        headers=headers,
        rows=rows,
        total_rows=len(frame),
    )


def _detect_columns(headers: list[str]) -> tuple[dict[str, str], list[MappingColumn]]:
    roles: dict[str, str] = {}
    mapping_columns: list[MappingColumn] = []
    for header in headers:
        role, confidence = match_column(header)
        if role and role not in roles:
            roles[role] = header
        mapping_columns.append(
            MappingColumn(
                source=header,
                target=role,
                confidence=confidence,
                sample="",
                status="mapped" if role else "unmapped",
            )
        )
    return roles, mapping_columns


def _build_fact(
    *,
    file_name: str,
    sheet: str,
    row_number: int,
    item: str,
    period_raw: str,
    raw_value: str,
    raw_unit: str,
    unit_scale: Decimal | None,
    unit_label: str,
    statement: str,
    note: str,
    field: str | None,
    field_status: str,
) -> Fact:
    period = parse_period(period_raw)
    value, value_status, embedded_note = _parse_number(raw_value)
    if value_status == "valid" and unit_scale is None:
        value_status = "invalid"
        embedded_note = "单位缺失或无法识别，不能安全转换为人民币元"
    if value_status == "valid" and value is not None and unit_scale is not None:
        value = value * unit_scale
    status = value_status
    if field_status in {"unmapped", "ambiguous"}:
        status = "unmapped"
    if field is None and field_status in {"mapped", "fuzzy"}:
        status = "unmapped"
    item_note = note
    if embedded_note:
        item_note = f"{note}；{embedded_note}".strip("；")
    if field_status == "ambiguous" and not item_note:
        item_note = "该名称存在多种会计含义，未自动映射。"
    return Fact(
        id=_fact_id(file_name, sheet, row_number, item, period_raw),
        statement=statement,  # type: ignore[arg-type]
        field=field,
        raw_item=item,
        period_raw=period_raw,
        period_start=period.start,
        period_end=period.end,
        period_kind=period.kind,
        period_label=period.label,
        value=value if value_status == "valid" else None,
        raw_value=_clean_cell(raw_value),
        unit_raw=raw_unit,
        unit=unit_label,
        source_file=file_name,
        source_sheet=sheet,
        source_row=row_number,
        status=status,  # type: ignore[arg-type]
        note=item_note,
    )


def _parse_long_frame(
    frame: pd.DataFrame,
    *,
    file_name: str,
    sheet: str,
    sheet_statement_hint: str,
    headers: list[str],
    roles: dict[str, str],
    mapping_columns: list[MappingColumn],
    issues: list[str],
) -> tuple[list[Fact], list[MappingItem], MappingInfo]:
    item_col = roles.get("item")
    value_col = roles.get("value")
    period_col = roles.get("period")
    unit_col = roles.get("unit")
    statement_col = roles.get("statement")
    note_col = roles.get("note")

    if not item_col or not value_col:
        return [], [], MappingInfo(columns=mapping_columns, statement_hint=sheet_statement_hint)

    facts: list[Fact] = []
    mapping_items: list[MappingItem] = []
    for row_index, row in frame.iterrows():
        excel_row = int(row_index) + 2
        item = _clean_cell(row.get(item_col, ""))
        if not item:
            continue
        period_raw = _clean_cell(row.get(period_col, "")) if period_col else ""
        if not period_raw:
            issues.append(f"{file_name} / {sheet} 第 {excel_row} 行缺少期间")
            continue
        value_raw = _clean_cell(row.get(value_col, ""))
        raw_unit = _clean_cell(row.get(unit_col, "")) if unit_col else ""
        note = _clean_cell(row.get(note_col, "")) if note_col else ""
        statement = (
            _statement_from_value(_clean_cell(row.get(statement_col, "")), sheet_statement_hint)
            if statement_col
            else sheet_statement_hint
        )
        field, confidence, field_status = match_field(item)
        unit_label, unit_scale, unit_note = _normalize_unit(raw_unit)
        if unit_note:
            note = f"{note}；{unit_note}".strip("；")
        fact = _build_fact(
            file_name=file_name,
            sheet=sheet,
            row_number=excel_row,
            item=item,
            period_raw=period_raw,
            raw_value=value_raw,
            raw_unit=raw_unit,
            unit_scale=unit_scale,
            unit_label=unit_label,
            statement=statement,
            note=note,
            field=field,
            field_status=field_status,
        )
        facts.append(fact)
        if field is not None or field_status in {"ambiguous", "unmapped"}:
            mapping_items.append(
                MappingItem(
                    raw_item=item,
                    field=field,
                    canonical_label=CANONICAL_LABELS.get(field or ""),
                    confidence=confidence,
                    status=field_status,
                    source_file=file_name,
                    source_row=excel_row,
                    sample_value=value_raw,
                    note=note,
                )
            )
    mapping = MappingInfo(
        layout="long",
        columns=mapping_columns,
        items=mapping_items,
        item_column=item_col,
        statement_hint=sheet_statement_hint,
        unit_hint=next((_clean_cell(row.get(unit_col, "")) for _, row in frame.iterrows() if unit_col), ""),
    )
    return facts, mapping_items, mapping


def _parse_wide_frame(
    frame: pd.DataFrame,
    *,
    file_name: str,
    sheet: str,
    sheet_statement_hint: str,
    headers: list[str],
    mapping_columns: list[MappingColumn],
    issues: list[str],
) -> tuple[list[Fact], list[MappingItem], MappingInfo]:
    item_col = next((header for header in headers if match_column(header)[0] == "item"), headers[0])
    period_columns = [
        header for header in headers if header != item_col and PERIOD_COLUMN_PATTERN.search(header)
    ]
    if not item_col or not period_columns:
        return [], [], MappingInfo(columns=mapping_columns, statement_hint=sheet_statement_hint)
    facts: list[Fact] = []
    mapping_items: list[MappingItem] = []
    for row_index, row in frame.iterrows():
        excel_row = int(row_index) + 2
        item = _clean_cell(row.get(item_col, ""))
        if not item:
            continue
        field, confidence, field_status = match_field(item)
        for period_col in period_columns:
            raw_value = _clean_cell(row.get(period_col, ""))
            fact = _build_fact(
                file_name=file_name,
                sheet=sheet,
                row_number=excel_row,
                item=item,
                period_raw=period_col,
                raw_value=raw_value,
                raw_unit="元",
                unit_scale=Decimal(1),
                unit_label="CNY",
                statement=sheet_statement_hint,
                note=f"宽表列：{period_col}",
                field=field,
                field_status=field_status,
            )
            facts.append(fact)
        if field is not None or field_status in {"ambiguous", "unmapped"}:
            mapping_items.append(
                MappingItem(
                    raw_item=item,
                    field=field,
                    canonical_label=CANONICAL_LABELS.get(field or ""),
                    confidence=confidence,
                    status=field_status,
                    source_file=file_name,
                    source_row=excel_row,
                    sample_value="；".join(_clean_cell(row.get(column, "")) for column in period_columns),
                )
            )
    mapping = MappingInfo(
        layout="wide",
        columns=mapping_columns,
        items=mapping_items,
        item_column=item_col,
        period_columns=period_columns,
        statement_hint=sheet_statement_hint,
    )
    return facts, mapping_items, mapping


def _parse_pdf(path: Path, file_name: str, statement_hint: str) -> tuple[list[Fact], list[MappingItem], MappingInfo, list[str]]:
    reader = PdfReader(str(path))
    issues: list[str] = []
    facts: list[Fact] = []
    mapping_items: list[MappingItem] = []
    pattern = re.compile(
        r"^\s*(?P<item>[一-龥A-Za-z（）()·、\-\s]{2,40}?)\s+"
        r"(?P<value>[（(]?[-−]?\d[\d,，]*\.?\d*[)）]?)\s*"
        r"(?P<unit>万元|千元|元)?\s*$"
    )
    period_pattern = re.compile(
        r"20\d{2}(?:[-/.]\d{1,2}[Mm]?(?:[-/.]\d{1,2})?|年?(?:前三季度|上半年|\d{1,2}月)|Q[1-4])"
    )
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not text.strip():
            issues.append(f"PDF 第 {page_number} 页没有可提取文本，扫描件需要 OCR，当前未启用。")
            continue
        period_match = period_pattern.search(text)
        period_raw = period_match.group(0) if period_match else ""
        if not period_raw:
            # Fall back to the filename when the PDF has no visible period.
            file_period = period_pattern.search(file_name)
            period_raw = file_period.group(0) if file_period else ""
        unit_hint_match = re.search(r"单位[:：]?\s*(万元|千元|元)", text)
        unit_hint = unit_hint_match.group(1) if unit_hint_match else "元"
        table_started = False
        for line_number, line in enumerate(text.splitlines(), start=1):
            match = pattern.match(line)
            if not match:
                if "项目" in line and ("金额" in line or "期末" in line):
                    table_started = True
                continue
            item = match.group("item").strip()
            if item in {"项目", "科目", "报表项目"}:
                table_started = True
                continue
            if not table_started and len(facts) == 0:
                # Accept well-formed lines even if no explicit header was found.
                table_started = True
            value = match.group("value")
            unit = match.group("unit") or unit_hint
            field, confidence, field_status = match_field(item)
            if not period_raw:
                issues.append(f"PDF 第 {page_number} 页第 {line_number} 行缺少可识别期间")
                continue
            fact = _build_fact(
                file_name=file_name,
                sheet=f"page-{page_number}",
                row_number=line_number,
                item=item,
                period_raw=period_raw,
                raw_value=value,
                raw_unit=unit,
                unit_scale=Decimal(1) if unit == "元" else Decimal(10000) if unit == "万元" else Decimal(1000),
                unit_label="CNY",
                statement=statement_hint,
                note="PDF 文本提取 · 请核对原始版面",
                field=field,
                field_status=field_status,
            )
            facts.append(fact)
            if field is not None or field_status in {"ambiguous", "unmapped"}:
                mapping_items.append(
                    MappingItem(
                        raw_item=item,
                        field=field,
                        canonical_label=CANONICAL_LABELS.get(field or ""),
                        confidence=confidence,
                        status=field_status,
                        source_file=file_name,
                        source_row=line_number,
                        sample_value=value,
                    )
                )
    if not facts:
        issues.append("PDF 未解析出可用的财务科目行。当前仅支持带可提取文本的简单报表。")
    mapping = MappingInfo(
        layout="pdf_text",
        items=mapping_items,
        statement_hint=statement_hint,
    )
    return facts, mapping_items, mapping, issues


def parse_files(
    file_specs: Iterable[tuple[Path, FileMeta]],
    *,
    dataset_id: str,
    company: str,
    is_demo: bool = False,
    source_label: str = "",
) -> Dataset:
    facts: list[Fact] = []
    previews: list[RawPreview] = []
    mapping_items: list[MappingItem] = []
    mapping_columns: list[MappingColumn] = []
    parse_issues: list[str] = []
    dataset = Dataset(
        id=dataset_id,
        company=company,
        name=company,
        is_demo=is_demo,
        source_label=source_label,
        files=[],
    )

    for path, meta in file_specs:
        dataset.files.append(meta)
        statement_hint = match_statement(path.stem)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            pdf_facts, pdf_items, pdf_mapping, issues = _parse_pdf(path, meta.name, statement_hint)
            facts.extend(pdf_facts)
            mapping_items.extend(pdf_items)
            mapping_columns.extend(pdf_mapping.columns)
            parse_issues.extend(issues)
            previews.append(
                RawPreview(
                    file=meta.name,
                    sheet="pdf_text",
                    headers=["文本预览"],
                    rows=[[issue] for issue in issues[:MAX_PREVIEW_ROWS]],
                    total_rows=len(issues),
                )
            )
            continue

        frames: dict[str, pd.DataFrame]
        if suffix == ".csv":
            frames = {"": _read_csv(path)}
        else:
            frames = _read_xlsx(path)
        for sheet_name, frame in frames.items():
            headers = _headers(frame)
            if not headers:
                parse_issues.append(f"{meta.name} / {sheet_name} 没有表头")
                continue
            previews.append(_preview(meta.name, sheet_name or "sheet1", frame))
            sheet_hint = match_statement(sheet_name) if sheet_name else statement_hint
            if sheet_hint == "unknown":
                sheet_hint = statement_hint
            roles, columns = _detect_columns(headers)
            mapping_columns.extend(columns)
            # Fill sample values for the column mapping display.
            for column in columns:
                if column.source in frame.columns and len(frame) > 0:
                    column.sample = _clean_cell(frame.iloc[0][column.source])
            if roles.get("value"):
                sheet_facts, sheet_items, sheet_mapping = _parse_long_frame(
                    frame,
                    file_name=meta.name,
                    sheet=sheet_name or "sheet1",
                    sheet_statement_hint=sheet_hint,
                    headers=headers,
                    roles=roles,
                    mapping_columns=columns,
                    issues=parse_issues,
                )
            else:
                sheet_facts, sheet_items, sheet_mapping = _parse_wide_frame(
                    frame,
                    file_name=meta.name,
                    sheet=sheet_name or "sheet1",
                    sheet_statement_hint=sheet_hint,
                    headers=headers,
                    mapping_columns=columns,
                    issues=parse_issues,
                )
                if not sheet_facts:
                    parse_issues.append(
                        f"{meta.name} / {sheet_name} 无法识别结构：需要金额列，或可识别的期间宽表列。"
                    )
            facts.extend(sheet_facts)
            mapping_items.extend(sheet_items)
            # Merge layout hints across sheets/files.
            dataset.mapping.layout = sheet_mapping.layout if dataset.mapping.layout == "long" else dataset.mapping.layout
            if sheet_mapping.item_column:
                dataset.mapping.item_column = sheet_mapping.item_column
            if sheet_mapping.period_columns:
                dataset.mapping.period_columns = sheet_mapping.period_columns

    dataset.facts = facts
    dataset.previews = previews
    dataset.mapping.columns = mapping_columns
    # Unique mapping rows by raw item + first source row; preserve the first traceable row.
    seen_items: set[tuple[str, str, int]] = set()
    unique_items: list[MappingItem] = []
    for item in mapping_items:
        key = (item.raw_item, item.source_file, item.source_row)
        if key in seen_items:
            continue
        seen_items.add(key)
        unique_items.append(item)
    dataset.mapping.items = unique_items
    period_tuples = sorted(
        {
            (fact.period_raw, fact.period_start, fact.period_end, fact.period_kind, fact.period_label)
            for fact in facts
            if fact.period_start and fact.period_end
        },
        key=lambda value: (value[2] or value[1], value[0]),
    )
    dataset.periods = [
        PeriodInfo(
            raw=raw,
            start=start,
            end=end,
            kind=kind,  # type: ignore[arg-type]
            label=label,
            days=(end - start).days + 1 if kind != "instant" else None,
        )
        for raw, start, end, kind, label in period_tuples
    ]
    if any(file.name.lower().endswith(".pdf") for file in dataset.files):
        dataset.mapping.layout = "pdf_text"
    dataset.quality = build_quality_issues(dataset)
    for issue in parse_issues:
        from finguard.models import QualityIssue

        dataset.quality.append(
            QualityIssue(
                id=f"DQ-PARSE-{abs(hash(issue)) % 100000:05d}",
                code="DQ-PARSE-WARNING",
                severity="warning",
                category="parse",
                message="文件解析提示",
                detail=issue,
                impact="请核对原始数据或文件结构；未识别的行不进入计算。",
            )
        )
    dataset.updated_at = datetime.now(dataset.created_at.tzinfo)
    return dataset


def apply_item_overrides(dataset: Dataset, overrides: dict[str, str | None]) -> Dataset:
    """Apply explicit human item mappings without guessing ambiguous labels.

    Only fields in CANONICAL_LABELS are accepted. Choosing ``None`` keeps the
    row unmapped. The dataset is re-checked after the change.
    """
    from finguard.ingestion.quality import build_quality_issues

    allowed = set(CANONICAL_LABELS)
    for raw_item, field in overrides.items():
        if field is not None and field not in allowed:
            raise ValueError(f"未知标准字段：{field}")
        for fact in dataset.facts:
            if fact.raw_item != raw_item:
                continue
            if field is None:
                fact.field = None
                fact.status = "unmapped"
                fact.note = "人工确认：保持未映射。"
            else:
                fact.field = field
                if fact.value is not None:
                    fact.status = "valid"
                elif fact.raw_value:
                    fact.status = "invalid"
                else:
                    fact.status = "missing"
                fact.note = "人工确认标准字段。"
        for item in dataset.mapping.items:
            if item.raw_item == raw_item:
                item.field = field
                item.canonical_label = CANONICAL_LABELS.get(field or "")
                item.status = "confirmed" if field else "confirmed_unmapped"
                item.confidence = 1.0 if field else 0.0
    dataset.quality = build_quality_issues(dataset)
    dataset.updated_at = datetime.now(dataset.created_at.tzinfo)
    return dataset
