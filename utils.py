from __future__ import annotations

import csv
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

from config import BLANK_MARKERS


def raise_csv_field_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def blank_to_empty(value: object) -> str:
    text = str(value or "").strip()
    if text.casefold() in BLANK_MARKERS:
        return ""
    return text


def normalize_date(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", value)
    if match:
        return match.group(1)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value


def normalize_time(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    match = re.search(r"(?:T|\b)(\d{1,2}:\d{2})(?::\d{2})?", value)
    if match:
        hour, minute = match.group(1).split(":")
        return f"{int(hour):02d}:{minute}"
    return value


def normalize_label(value: str) -> str:
    cleaned = (
        str(value or "")
        .replace("\u2019", "'")
        .replace("`", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .strip()
        .lower()
    )
    cleaned = re.sub(r"'s\b", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    parts = []
    for part in cleaned.split():
        if len(part) > 4 and part.endswith("s") and not part.endswith("ss"):
            part = part[:-1]
        parts.append(part)
    return " ".join(parts)


def normalized_tokens(value: str) -> Tuple[str, ...]:
    from config import STOPWORDS

    raw_parts = re.split(r"[^A-Za-z0-9]+", str(value or ""))
    tokens: List[str] = []
    for raw in raw_parts:
        token = normalize_token(raw)
        if not token or token in STOPWORDS:
            continue
        tokens.append(token)
    return tuple(tokens)


def normalize_token(token: str) -> str:
    cleaned = (
        token.replace("\u2019", "'")
        .replace("`", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .strip()
        .lower()
    )
    cleaned = re.sub(r"'s\b", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", "", cleaned)
    if len(cleaned) > 4 and cleaned.endswith("s") and not cleaned.endswith("ss"):
        cleaned = cleaned[:-1]
    return cleaned


def token_signature(value: str) -> Tuple[str, ...]:
    return tuple(sorted(set(normalized_tokens(value))))


def strip_bracket_suffix(value: str) -> str:
    return re.sub(r"\s*\[[^\]]+\]\s*$", "", value or "").strip()


def extract_bracket_label(value: str) -> str:
    match = re.search(r"\[([^\]]+)\]\s*$", value or "")
    return match.group(1).strip() if match else ""


def read_shared_strings(root: ET.Element) -> List[str]:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values: List[str] = []
    for item in root.findall("main:si", namespace):
        text_parts = [node.text or "" for node in item.findall(".//main:t", namespace)]
        values.append("".join(text_parts))
    return values


def column_index_from_ref(cell_ref: str) -> int:
    letters = re.sub(r"\d", "", cell_ref or "")
    index = 0
    for char in letters:
        index = index * 26 + (ord(char.upper()) - ord("A") + 1)
    return max(index - 1, 0)


def read_xlsx_rows(path: Path) -> List[List[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required workbook not found: {path}")

    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: List[List[str]] = []

    try:
        with zipfile.ZipFile(path) as workbook:
            shared_strings: List[str] = []
            if "xl/sharedStrings.xml" in workbook.namelist():
                shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
                shared_strings = read_shared_strings(shared_root)

            sheet_name = "xl/worksheets/sheet1.xml"
            if sheet_name not in workbook.namelist():
                raise RuntimeError(f"Workbook is missing {sheet_name}: {path}")

            sheet_root = ET.fromstring(workbook.read(sheet_name))
            for row in sheet_root.findall("main:sheetData/main:row", namespace):
                values: Dict[int, str] = {}
                for cell in row.findall("main:c", namespace):
                    cell_type = cell.attrib.get("t", "")
                    cell_ref = cell.attrib.get("r", "")
                    index = column_index_from_ref(cell_ref)
                    value = ""
                    if cell_type == "inlineStr":
                        value = "".join(
                            node.text or ""
                            for node in cell.findall(".//main:t", namespace)
                        ).strip()
                    else:
                        raw_value = cell.find("main:v", namespace)
                        if raw_value is not None and raw_value.text is not None:
                            if cell_type == "s":
                                shared_index = int(raw_value.text)
                                value = (
                                    shared_strings[shared_index]
                                    if 0 <= shared_index < len(shared_strings)
                                    else ""
                                )
                            else:
                                value = raw_value.text.strip()
                    values[index] = value

                if not values:
                    rows.append([])
                    continue

                max_index = max(values.keys())
                rows.append([values.get(i, "") for i in range(max_index + 1)])
    except PermissionError as exc:
        raise RuntimeError(
            f"Could not open workbook '{path.name}'. Close it in Excel and try again."
        ) from exc
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"'{path.name}' is not a valid .xlsx workbook.") from exc

    return rows


def row_to_dict(row: Sequence[str], headers: Sequence[str]) -> Dict[str, str]:
    item: Dict[str, str] = {}
    for index, header in enumerate(headers):
        item[header] = row[index].strip() if index < len(row) else ""
    return item


def clean_csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[\r\n\t]+", " ", text).strip()
    return text.replace(",", ";")


def deduplicate(values: object) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def find_mapping_header(headers: Sequence[str], candidates: Sequence[str]) -> str:
    normalized_headers = {normalize_label(header): header for header in headers}
    for candidate in candidates:
        header = normalized_headers.get(normalize_label(candidate))
        if header:
            return header
    return ""


def build_header_info(headers: Sequence[str]) -> List[HeaderInfo]:
    from models import HeaderInfo

    info: List[HeaderInfo] = []
    for header in headers:
        base_name = strip_bracket_suffix(header)
        info.append(
            HeaderInfo(
                header=header,
                base_name=base_name,
                normalized_header=normalize_label(header),
                normalized_base=normalize_label(base_name),
                header_tokens=token_signature(header),
                base_tokens=token_signature(base_name),
                source_label=normalize_label(extract_bracket_label(header)),
            )
        )
    return info


def find_exact_header(candidate: str, header_info: Sequence[HeaderInfo]) -> str:
    if not candidate:
        return ""
    candidate = candidate.strip()
    candidate_base = strip_bracket_suffix(candidate)
    candidate_label = normalize_label(extract_bracket_label(candidate))
    candidate_norm = normalize_label(candidate)
    candidate_base_norm = normalize_label(candidate_base)

    exact = [item.header for item in header_info if item.header == candidate]
    if len(exact) == 1:
        return exact[0]

    exact_base_items = [item for item in header_info if item.base_name == candidate_base]
    if len(exact_base_items) == 1:
        item = exact_base_items[0]
        if not candidate_label or not item.source_label or item.source_label == candidate_label:
            return item.header
    if len(exact_base_items) > 1 and candidate_label:
        label_match = [
            item.header
            for item in exact_base_items
            if item.source_label == candidate_label
        ]
        if len(label_match) == 1:
            return label_match[0]

    normalized_items = [
        item
        for item in header_info
        if item.normalized_header == candidate_norm or item.normalized_base == candidate_base_norm
    ]
    if len(normalized_items) == 1:
        item = normalized_items[0]
        if not candidate_label or not item.source_label or item.source_label == candidate_label:
            return item.header
    if len(normalized_items) > 1 and candidate_label:
        label_match = [
            item.header
            for item in normalized_items
            if item.source_label == candidate_label
        ]
        if len(label_match) == 1:
            return label_match[0]

    return ""


def is_mapping_source_column(header: str, stage_header: str, data_element_header: str) -> bool:
    if not header or header in {stage_header, data_element_header}:
        return False
    normalized = normalize_label(header)
    ignored = {
        "dhis2 program stage id",
        "program stage id",
        "dhis2 data element id",
        "data element id",
        "stage id",
        "section name",
        "form name",
        "data type",
        "options",
        "notes",
        "remark",
        "remarks",
    }
    return normalized not in ignored
