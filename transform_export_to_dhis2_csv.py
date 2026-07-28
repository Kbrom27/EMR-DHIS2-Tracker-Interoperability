from __future__ import annotations

import argparse
import csv
import re
import sys
import threading
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from itertools import chain
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError:
    tk = None
    filedialog = None
    messagebox = None
    ttk = None

from tracker_mapping_rules import (
    apply_field_alias,
    get_external_field_transform,
    get_field_transform,
    get_preferred_source_headers,
    resolve_configured_option_value,
    resolve_external_value_mapping,
    set_value_mapping_path,
    should_suppress_value,
    uses_strict_preferred_sources,
)


MATERNAL_PROGRAM = "Maternal Inpatient Data/aLoraiFNkng"
NEONATAL_PROGRAM = "Neonatal Care Form/QYJKpoUeg9F"
SPECIAL_COLUMNS = ["org_unit", "program", "Record ID"]
CONTEXT_COLUMNS = ["visit_date"]
HEADER_SEPARATOR = " :: "
BLANK_MARKERS = {"", "none", "null", "nan", "n/a"}
STOPWORDS = {"a", "an", "at", "for", "in", "n", "of", "on", "the", "to"}
RESOURCES_DIR = Path(__file__).resolve().with_name("Resources")

# Default program specs (for backward compatibility)
PROGRAM_SPECS = {
    MATERNAL_PROGRAM: {
        "mapping_path": RESOURCES_DIR / "EMR-DHIS2 Tracker Maternal Mapping.xlsx",
        "dictionary_path": RESOURCES_DIR / "MID data disctionary.xlsx",
    },
    NEONATAL_PROGRAM: {
        "mapping_path": RESOURCES_DIR / "EMR-DHIS2 Tracker Neonatal Mapping.xlsx",
        "dictionary_path": RESOURCES_DIR / "NCF data disctionary.xlsx",
    },
}

# User-provided mapping files
_user_mapping_excel_path: Optional[Path] = None
_user_dictionary_excel_path: Optional[Path] = None
_user_value_mapping_csv_path: Optional[Path] = None

TARGETED_DICTIONARY_STAGE_FALLBACKS = {
    MATERNAL_PROGRAM: {"Laboratory", "Physicians Medication Order"},
    NEONATAL_PROGRAM: {"Investigation sheet"},
}

AGGREGATE_SOURCE_OVERRIDES = {
    (MATERNAL_PROGRAM, "Laboratory", "Laboratory event date"): "visit_date",
    (MATERNAL_PROGRAM, "Laboratory", "Other Laboratory Investigations"): "lab_results",
    (MATERNAL_PROGRAM, "Physicians Medication Order", "Physician Medication order event date"): "visit_date",
    (MATERNAL_PROGRAM, "Physicians Medication Order", "Medication order date"): "visit_date",
    (MATERNAL_PROGRAM, "Physicians Medication Order", "Ordered medication name"): "medications",
    (NEONATAL_PROGRAM, "Investigation sheet", "other inv n"): "lab_results",
}

FIELD_SOURCE_ALIASES = {
    "neonate first name": "first_name",
    "neonate last name": "family_name",
    "neonate mrn": "patient_id",
    "neonate sex": "gender",
}

MATERNAL_DIAGNOSIS_SOURCE_HEADERS = ("diagnoses",)
DIAGNOSIS_OBSTETRIC_COMPLICATIONS_HEADER = "Diagnosis :: Obstetric complications"
DIAGNOSIS_AMNIOTIC_FLUID_HEADER = "Diagnosis :: Amniotic fluid abnormalities"
DIAGNOSIS_OBSTETRIC_COMPLICATIONS_OTHER_HEADER = "Diagnosis :: Obstetric complications Others"
MATERNAL_COMPUTED_DIAGNOSIS_HEADERS = (
    DIAGNOSIS_OBSTETRIC_COMPLICATIONS_HEADER,
    DIAGNOSIS_AMNIOTIC_FLUID_HEADER,
    DIAGNOSIS_OBSTETRIC_COMPLICATIONS_OTHER_HEADER,
)
DIAGNOSIS_METADATA_VALUES = {
    "primary",
    "secondary",
    "confirmed",
    "presumed",
    "false",
    "true",
}
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def set_mapping_files(mapping_excel_path: Path, dictionary_excel_path: Path, value_mapping_csv_path: Optional[Path] = None) -> None:
    """Set user-provided mapping files"""
    global _user_mapping_excel_path, _user_dictionary_excel_path, _user_value_mapping_csv_path, PROGRAM_SPECS
    _user_mapping_excel_path = mapping_excel_path
    _user_dictionary_excel_path = dictionary_excel_path
    _user_value_mapping_csv_path = value_mapping_csv_path
    
    # Use the same files for both programs (the program is auto-detected from CSV)
    PROGRAM_SPECS = {
        MATERNAL_PROGRAM: {
            "mapping_path": mapping_excel_path,
            "dictionary_path": dictionary_excel_path,
        },
        NEONATAL_PROGRAM: {
            "mapping_path": mapping_excel_path,
            "dictionary_path": dictionary_excel_path,
        },
    }
    
    # Set value mapping path if provided
    if value_mapping_csv_path:
        set_value_mapping_path(value_mapping_csv_path)


@dataclass
class DictionaryField:
    stage_name: str
    data_element_name: str
    data_element_id: str
    form_name: str
    data_type: str
    options_text: str


@dataclass
class MappingField:
    stage_name: str
    data_element_name: str
    target_header: str
    source_name: str
    form_name: str
    data_type: str
    options_text: str
    org_unit: str = ""
    source_header: str = ""


@dataclass(frozen=True)
class HeaderInfo:
    header: str
    base_name: str
    normalized_header: str
    normalized_base: str
    header_tokens: Tuple[str, ...]
    base_tokens: Tuple[str, ...]
    source_label: str


def raise_csv_field_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def normalize_program_value(value: str) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    lower = cleaned.casefold()
    if "aloraifnkng" in lower or "maternal inpatient data" in lower:
        return MATERNAL_PROGRAM
    if "qyjkpoueg9f" in lower or "neonatal care form" in lower:
        return NEONATAL_PROGRAM
    return cleaned


def deduplicate(values: Iterable[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def blank_to_empty(value: object) -> str:
    text = str(value or "").strip()
    if text.casefold() in BLANK_MARKERS:
        return ""
    return text


def strip_bracket_suffix(value: str) -> str:
    return re.sub(r"\s*\[[^\]]+\]\s*$", "", value or "").strip()


def extract_bracket_label(value: str) -> str:
    match = re.search(r"\[([^\]]+)\]\s*$", value or "")
    return match.group(1).strip() if match else ""


def normalize_token(token: str) -> str:
    cleaned = (
        token.replace("’", "'")
        .replace("`", "'")
        .replace("“", '"')
        .replace("”", '"')
        .strip()
        .lower()
    )
    cleaned = re.sub(r"'s\b", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", "", cleaned)
    if len(cleaned) > 4 and cleaned.endswith("s") and not cleaned.endswith("ss"):
        cleaned = cleaned[:-1]
    return cleaned


def normalized_tokens(value: str) -> Tuple[str, ...]:
    raw_parts = re.split(r"[^A-Za-z0-9]+", str(value or ""))
    tokens: List[str] = []
    for raw in raw_parts:
        token = normalize_token(raw)
        if not token or token in STOPWORDS:
            continue
        tokens.append(token)
    return tuple(tokens)


def normalize_label(value: str) -> str:
    return " ".join(normalized_tokens(value))


def token_signature(value: str) -> Tuple[str, ...]:
    return tuple(sorted(set(normalized_tokens(value))))


def build_header_info(headers: Sequence[str]) -> List[HeaderInfo]:
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


def read_dictionary_fields(path: Path) -> Dict[Tuple[str, str], DictionaryField]:
    rows = read_xlsx_rows(path)
    if not rows:
        raise RuntimeError(f"No rows were found in {path.name}.")

    headers = rows[0]
    fields: Dict[Tuple[str, str], DictionaryField] = {}
    for row in rows[1:]:
        item = row_to_dict(row, headers)
        stage_name = item.get("Stage Name", "").strip()
        data_element_name = item.get("Data Element Name", "").strip()
        if not stage_name or not data_element_name:
            continue
        fields[(stage_name, data_element_name)] = DictionaryField(
            stage_name=stage_name,
            data_element_name=data_element_name,
            data_element_id=item.get("Data Element ID", "").strip(),
            form_name=item.get("Form Name", "").strip(),
            data_type=item.get("Data Type", "").strip(),
            options_text=item.get("Options", "").strip(),
        )
    return fields


def find_mapping_header(headers: Sequence[str], candidates: Sequence[str]) -> str:
    normalized_headers = {normalize_label(header): header for header in headers}
    for candidate in candidates:
        header = normalized_headers.get(normalize_label(candidate))
        if header:
            return header
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


def source_column_org_unit(header: str) -> str:
    bracket_label = extract_bracket_label(header)
    if bracket_label:
        return bracket_label

    generic_headers = {"emr concept name", "emr concept", "concept name"}
    normalized = normalize_label(header)
    if normalized in generic_headers:
        return ""

    facility_tokens = [
        token
        for token in normalized_tokens(header)
        if token not in {"emr", "concept", "name", "source", "mapping", "column", "facility"}
    ]
    return " ".join(facility_tokens) or header.strip()


def read_mapping_fields(path: Path, dictionary_fields: Dict[Tuple[str, str], DictionaryField]) -> List[MappingField]:
    rows = read_xlsx_rows(path)
    if not rows:
        raise RuntimeError(f"No rows were found in {path.name}.")

    headers = rows[0]
    stage_header = find_mapping_header(
        headers,
        ("DHIS2 Program Stage Name", "Program Stage Name", "Stage Name"),
    )
    data_element_header = find_mapping_header(
        headers,
        ("DHIS2 Data Element Name", "Data Element Name"),
    )
    source_headers = [
        header
        for header in headers
        if is_mapping_source_column(header, stage_header, data_element_header)
    ]

    if not stage_header or not data_element_header or not source_headers:
        raise RuntimeError(
            f"{path.name} must contain stage, DHIS2 data element, and EMR concept columns."
        )

    mapping_fields: List[MappingField] = []
    seen_targets = set()

    for row in rows[1:]:
        item = row_to_dict(row, headers)
        stage_name = item.get(stage_header, "").strip()
        data_element_name = item.get(data_element_header, "").strip()
        if not stage_name or not data_element_name:
            continue

        dictionary_field = dictionary_fields.get((stage_name, data_element_name))
        if dictionary_field is None:
            continue

        target_header = f"{stage_name}{HEADER_SEPARATOR}{data_element_name}"
        for source_column in source_headers:
            source_name = item.get(source_column, "").strip()
            if not source_name:
                continue

            org_unit = source_column_org_unit(source_column)
            key = (target_header, org_unit, source_name)
            if key in seen_targets:
                continue
            seen_targets.add(key)

            mapping_fields.append(
                MappingField(
                    stage_name=stage_name,
                    data_element_name=data_element_name,
                    target_header=target_header,
                    source_name=source_name,
                    form_name=dictionary_field.form_name,
                    data_type=dictionary_field.data_type,
                    options_text=dictionary_field.options_text,
                    org_unit=org_unit,
                )
            )

    return mapping_fields


def add_dictionary_stage_fallbacks(
    program: str,
    mapping_fields: List[MappingField],
    dictionary_fields: Dict[Tuple[str, str], DictionaryField],
) -> List[MappingField]:
    fallback_stages = TARGETED_DICTIONARY_STAGE_FALLBACKS.get(program, set())
    if not fallback_stages:
        return mapping_fields

    fields = list(mapping_fields)
    seen = {
        (field.target_header, field.org_unit, field.source_name)
        for field in fields
    }
    for (stage_name, data_element_name), dictionary_field in dictionary_fields.items():
        if stage_name not in fallback_stages:
            continue
        source_name = AGGREGATE_SOURCE_OVERRIDES.get(
            (program, stage_name, data_element_name),
            dictionary_field.form_name or data_element_name,
        )
        if not source_name:
            continue
        target_header = f"{stage_name}{HEADER_SEPARATOR}{data_element_name}"
        key = (target_header, "", source_name)
        if key in seen:
            continue
        seen.add(key)
        fields.append(
            MappingField(
                stage_name=stage_name,
                data_element_name=data_element_name,
                target_header=target_header,
                source_name=source_name,
                form_name=dictionary_field.form_name,
                data_type=dictionary_field.data_type,
                options_text=dictionary_field.options_text,
            )
        )

    return fields


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

    exact_base = [item.header for item in header_info if item.base_name == candidate_base]
    if len(exact_base) == 1:
        return exact_base[0]
    if len(exact_base) > 1 and candidate_label:
        label_match = [
            item.header
            for item in header_info
            if item.base_name == candidate_base and item.source_label == candidate_label
        ]
        if len(label_match) == 1:
            return label_match[0]

    normalized = [
        item.header
        for item in header_info
        if item.normalized_header == candidate_norm or item.normalized_base == candidate_base_norm
    ]
    if len(normalized) == 1:
        return normalized[0]
    if len(normalized) > 1 and candidate_label:
        label_match = [
            item.header
            for item in header_info
            if (
                item.normalized_header == candidate_norm
                or item.normalized_base == candidate_base_norm
            )
            and item.source_label == candidate_label
        ]
        if len(label_match) == 1:
            return label_match[0]

    return ""


def resolve_alias_source_header(field: MappingField, header_info: Sequence[HeaderInfo]) -> str:
    alias = FIELD_SOURCE_ALIASES.get(normalize_label(field.data_element_name))
    if not alias:
        return ""
    return find_exact_header(alias, header_info)


def score_header_match(
    candidate: str,
    header: HeaderInfo,
    stage_name: str,
    form_name: str,
    has_explicit_source: bool,
) -> float:
    candidate_base = strip_bracket_suffix(candidate)
    candidate_norm = normalize_label(candidate_base)
    if not candidate_norm:
        return 0.0

    candidate_tokens = token_signature(candidate_base)
    score = 0.0

    if header.normalized_header == candidate_norm or header.normalized_base == candidate_norm:
        score = 1.0
    elif candidate_tokens and header.base_tokens == candidate_tokens:
        score = 0.97
    elif candidate_tokens and set(candidate_tokens).issubset(set(header.base_tokens)):
        score = 0.91
    else:
        score = SequenceMatcher(None, candidate_norm, header.normalized_base).ratio()

    if form_name and header.source_label:
        score += 0.03 * SequenceMatcher(None, normalize_label(form_name), header.source_label).ratio()
    if stage_name and header.source_label:
        score += 0.02 * SequenceMatcher(None, normalize_label(stage_name), header.source_label).ratio()
    if has_explicit_source:
        score += 0.01

    return score


def resolve_source_header(field: MappingField, header_info: Sequence[HeaderInfo]) -> str:
    preferred_candidates = deduplicate(get_preferred_source_headers(field.target_header))
    strict_preferred_sources = uses_strict_preferred_sources(field.target_header)
    for candidate in preferred_candidates:
        exact = find_exact_header(candidate, header_info)
        if exact:
            return exact
    if strict_preferred_sources:
        return ""

    preferred_best_header = ""
    preferred_best_score = 0.0
    for candidate in preferred_candidates:
        for header in header_info:
            score = score_header_match(
                candidate=candidate,
                header=header,
                stage_name=field.stage_name,
                form_name=field.form_name,
                has_explicit_source=True,
            )
            if score > preferred_best_score:
                preferred_best_score = score
                preferred_best_header = header.header
    if preferred_best_score >= 0.82:
        return preferred_best_header

    alias_header = resolve_alias_source_header(field, header_info)
    if alias_header:
        return alias_header

    candidates = deduplicate(
        value
        for value in (
            field.source_name,
            field.form_name,
            field.data_element_name,
        )
        if value
    )

    for candidate in candidates:
        exact = find_exact_header(candidate, header_info)
        if exact:
            return exact

    best_header = ""
    best_score = 0.0
    for candidate in candidates:
        for header in header_info:
            score = score_header_match(
                candidate=candidate,
                header=header,
                stage_name=field.stage_name,
                form_name=field.form_name,
                has_explicit_source=bool(field.source_name),
            )
            if score > best_score:
                best_score = score
                best_header = header.header

    threshold = 0.82 if field.source_name else 0.90
    return best_header if best_score >= threshold else ""


def select_mapping_field(
    fields: Sequence[MappingField],
    org_unit: str,
    target_header: str,
) -> Optional[MappingField]:
    candidates = [
        field for field in fields if field.target_header == target_header and field.source_header
    ]
    if not candidates:
        return None

    normalized_org = str(org_unit or "").strip().casefold()
    normalized_org_label = normalize_label(org_unit)
    if normalized_org:
        for field in candidates:
            if (
                field.org_unit.casefold() == normalized_org
                or normalize_label(field.org_unit) == normalized_org_label
            ):
                return field

    for field in candidates:
        if not field.org_unit:
            return field

    return candidates[0]


def split_export_values(raw_value: str) -> List[str]:
    text = str(raw_value or "").strip()
    if not text:
        return []
    parts = [blank_to_empty(part) for part in text.split(" | ")]
    return [part for part in parts if part]


def last_export_value(raw_value: str) -> str:
    values = split_export_values(raw_value)
    return values[-1] if values else blank_to_empty(raw_value)


def parse_options(options_text: str) -> Tuple[Dict[str, str], Dict[str, str], List[Tuple[Tuple[str, ...], str]]]:
    code_map: Dict[str, str] = {}
    label_map: Dict[str, str] = {}
    token_map: List[Tuple[Tuple[str, ...], str]] = []
    for option in str(options_text or "").split(";"):
        option = option.strip()
        if not option:
            continue
        if ":" in option:
            code, label = option.split(":", 1)
        else:
            code = option
            label = option
        code = code.strip()
        label = label.strip()
        canonical = label or code
        if not canonical:
            continue
        if code:
            code_map[code.casefold()] = canonical
            label_map[code.casefold()] = canonical
        label_map[normalize_label(code)] = canonical
        label_map[canonical.casefold()] = canonical
        label_map[normalize_label(canonical)] = canonical
        token_map.append((token_signature(canonical), canonical))
    return code_map, label_map, token_map


def normalize_boolean_token(value: str) -> Optional[str]:
    normalized = normalize_label(value)
    if normalized in {"1", "true", "t", "yes", "y"}:
        return "true"
    if normalized in {"0", "false", "f", "no", "n"}:
        return "false"
    return None


def resolve_option_name(
    part: str,
    code_map: Dict[str, str],
    label_map: Dict[str, str],
    token_map: Sequence[Tuple[Tuple[str, ...], str]],
) -> str:
    exact_code = code_map.get(part.casefold())
    if exact_code:
        return exact_code

    exact_label = label_map.get(part.casefold())
    if exact_label:
        return exact_label

    normalized = normalize_label(part)
    mapped = label_map.get(normalized)
    if mapped:
        return mapped

    boolean_value = normalize_boolean_token(part)
    if boolean_value == "true" and "1" in code_map:
        return code_map["1"]
    if boolean_value == "false" and "0" in code_map:
        return code_map["0"]

    part_tokens = set(token_signature(part))
    best_label = ""
    best_score = 0.0
    part_normalized = normalize_label(part)
    for option_tokens, option_label in token_map:
        if not option_tokens:
            continue
        option_token_set = set(option_tokens)
        if part_tokens and option_token_set.issubset(part_tokens):
            score = 0.9 + (len(option_token_set) / max(len(part_tokens), 1)) * 0.05
        else:
            score = SequenceMatcher(None, part_normalized, normalize_label(option_label)).ratio()
        if score > best_score:
            best_score = score
            best_label = option_label

    return best_label if best_score >= 0.84 else ""


def normalize_option_value(
    raw_value: str,
    data_type: str,
    options_text: str,
    target_header: str,
) -> str:
    code_map, label_map, token_map = parse_options(options_text)
    parts = split_export_values(raw_value)
    if not parts:
        return ""

    multi_value = data_type == "MULTI_TEXT"
    mapped_values: List[str] = []

    for part in parts if multi_value else [parts[-1]]:
        configured = resolve_configured_option_value(
            raw_value=part,
            options_text=options_text,
            target_header=target_header,
            return_codes=False,
        )
        if configured:
            mapped_values.append(configured)
            continue

        mapped = resolve_option_name(part, code_map, label_map, token_map)
        if mapped:
            mapped_values.append(mapped)
            continue

        mapped_values.append(part.strip())

    mapped_values = deduplicate(value for value in mapped_values if value)
    if not mapped_values:
        return ""
    if multi_value:
        return ";".join(mapped_values)
    return mapped_values[-1]


def normalize_integer(raw_value: str) -> str:
    value = last_export_value(raw_value).replace(",", "")
    if not value:
        return ""
    try:
        decimal_value = Decimal(value)
    except InvalidOperation:
        fallback = re.sub(r"[^0-9+-]", "", value)
        if fallback and fallback not in {"+", "-"}:
            return fallback
        return value
    return str(int(decimal_value))


def normalize_number(raw_value: str) -> str:
    value = last_export_value(raw_value).replace(",", "")
    if not value:
        return ""
    try:
        decimal_value = Decimal(value)
    except InvalidOperation:
        fallback = re.sub(r"[^0-9+-.]", "", value)
        if fallback and fallback not in {"+", "-", ".", "+.", "-."}:
            value = fallback
            try:
                decimal_value = Decimal(value)
            except InvalidOperation:
                return value
        else:
            return value
    normalized = format(decimal_value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def normalize_date(raw_value: str) -> str:
    value = last_export_value(raw_value)
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
    value = last_export_value(raw_value)
    if not value:
        return ""
    match = re.search(r"(?:T|\b)(\d{1,2}:\d{2})(?::\d{2})?", value)
    if match:
        hour, minute = match.group(1).split(":")
        return f"{int(hour):02d}:{minute}"
    return value


def normalize_datetime_value(raw_value: str) -> str:
    value = last_export_value(raw_value)
    if not value:
        return ""
    date_value = normalize_date(value)
    time_value = normalize_time(value)
    if date_value and time_value:
        return f"{date_value}T{time_value}:00"
    return value


def normalize_text_value(raw_value: str) -> str:
    return last_export_value(raw_value)


def normalize_tracker_value(
    raw_value: str,
    data_type: str,
    options_text: str,
    target_header: str = "",
    program: str = "",
) -> str:
    value = str(raw_value or "").strip()
    if not value or value.casefold() in BLANK_MARKERS:
        return ""
    if should_suppress_value(value, target_header):
        return ""

    external_value = resolve_external_value_mapping(value, target_header, program)
    if external_value:
        return external_value

    configured_transform = get_external_field_transform(target_header, program) or get_field_transform(target_header)
    if configured_transform == "date":
        return normalize_date(value)
    if configured_transform == "time":
        return normalize_time(value)
    if configured_transform == "datetime":
        return normalize_datetime_value(value)
    if configured_transform == "all_text":
        return value

    if options_text:
        return normalize_option_value(value, data_type, options_text, target_header)

    if data_type in {"BOOLEAN", "TRUE_ONLY"}:
        normalized = normalize_boolean_token(value)
        if data_type == "TRUE_ONLY":
            return "true" if normalized == "true" else ""
        return normalized or last_export_value(value)

    if data_type in {"INTEGER", "INTEGER_ZERO_OR_POSITIVE", "INTEGER_POSITIVE", "INTEGER_NEGATIVE"}:
        return normalize_integer(value)

    if data_type in {"NUMBER", "PERCENTAGE", "UNIT_INTERVAL"}:
        return normalize_number(value)

    if data_type == "DATE":
        return normalize_date(value)

    if data_type == "TIME":
        return normalize_time(value)

    if data_type == "DATETIME":
        return normalize_datetime_value(value)

    configured_text = apply_field_alias(normalize_text_value(value), target_header)
    if configured_text:
        return configured_text

    return normalize_text_value(value)


def is_diagnosis_metadata_value(value: str) -> bool:
    cleaned = str(value or "").strip()
    if not cleaned:
        return True
    normalized = normalize_label(cleaned)
    return normalized in DIAGNOSIS_METADATA_VALUES or bool(UUID_PATTERN.fullmatch(cleaned))


def split_diagnosis_values(raw_value: str) -> List[str]:
    values: List[str] = []
    for part in str(raw_value or "").split(" | "):
        value = blank_to_empty(part)
        if not value or is_diagnosis_metadata_value(value):
            continue
        values.append(value)
    return values


def get_maternal_diagnosis_source(row: Dict[str, str]) -> str:
    for header in MATERNAL_DIAGNOSIS_SOURCE_HEADERS:
        value = row.get(header, "")
        if blank_to_empty(value):
            return value
    return ""


def append_deduped(values: List[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def map_maternal_diagnosis_values(raw_value: str) -> Tuple[str, str, str]:
    obstetric_complications: List[str] = []
    amniotic_fluid_abnormalities: List[str] = []
    other_values: List[str] = []

    for value in split_diagnosis_values(raw_value):
        normalized = normalize_label(value)
        recognized = False

        if re.search(r"\bgdm\b", normalized):
            append_deduped(obstetric_complications, "GDM")
            recognized = True
        if re.search(r"\baph\b", normalized):
            append_deduped(obstetric_complications, "APH")
            recognized = True
        if re.search(r"\bprom\b", normalized):
            append_deduped(obstetric_complications, "PROM")
            recognized = True
        if "oligohydramnio" in normalized:
            append_deduped(obstetric_complications, "Amniotic fluid abnormalities")
            append_deduped(amniotic_fluid_abnormalities, "Oligohydramnios")
            recognized = True
        if "polyhydramnio" in normalized:
            append_deduped(obstetric_complications, "Amniotic fluid abnormalities")
            append_deduped(amniotic_fluid_abnormalities, "Polyhydramnios")
            recognized = True

        if not recognized:
            append_deduped(other_values, value)

    if other_values:
        append_deduped(obstetric_complications, "Others (specify)")

    return (
        ";".join(obstetric_complications),
        ";".join(amniotic_fluid_abnormalities),
        "; ".join(other_values),
    )


def apply_maternal_diagnosis_transform(
    transformed_row: "OrderedDict[str, str]",
    source_row: Dict[str, str],
) -> None:
    raw_diagnoses = get_maternal_diagnosis_source(source_row)
    if not blank_to_empty(raw_diagnoses):
        return

    complications, amniotic_abnormalities, others = map_maternal_diagnosis_values(raw_diagnoses)
    transformed_row[DIAGNOSIS_OBSTETRIC_COMPLICATIONS_HEADER] = complications
    transformed_row[DIAGNOSIS_AMNIOTIC_FLUID_HEADER] = amniotic_abnormalities
    transformed_row[DIAGNOSIS_OBSTETRIC_COMPLICATIONS_OTHER_HEADER] = others


def load_program_fields(programs: Sequence[str] | None = None) -> Dict[str, List[MappingField]]:
    program_fields: Dict[str, List[MappingField]] = {}
    selected_programs = programs or tuple(PROGRAM_SPECS.keys())
    for program in selected_programs:
        spec = PROGRAM_SPECS[program]
        dictionary_fields = read_dictionary_fields(spec["dictionary_path"])
        mapping_fields = read_mapping_fields(spec["mapping_path"], dictionary_fields)
        program_fields[program] = add_dictionary_stage_fallbacks(
            program,
            mapping_fields,
            dictionary_fields,
        )
    return program_fields


def resolve_program_sources(
    program_fields: Dict[str, List[MappingField]],
    export_headers: Sequence[str],
    programs: Sequence[str] | None = None,
) -> Tuple[Dict[str, List[MappingField]], Dict[str, List[str]]]:
    header_info = build_header_info(export_headers)
    resolved_fields: Dict[str, List[MappingField]] = {}
    missing_fields: Dict[str, List[str]] = {}

    selected_programs = programs or tuple(program_fields.keys())
    for program in selected_programs:
        fields = program_fields[program]
        resolved: List[MappingField] = []
        missing: List[str] = []
        for field in fields:
            source_header = resolve_source_header(field, header_info)
            resolved_field = MappingField(
                stage_name=field.stage_name,
                data_element_name=field.data_element_name,
                target_header=field.target_header,
                source_name=field.source_name,
                form_name=field.form_name,
                data_type=field.data_type,
                options_text=field.options_text,
                org_unit=field.org_unit,
                source_header=source_header,
            )
            if source_header:
                resolved.append(resolved_field)
            else:
                missing.append(resolved_field.target_header)
        resolved_fields[program] = resolved
        missing_fields[program] = missing

    return resolved_fields, missing_fields


def transform_rows(
    input_path: Path,
    output_path: Path,
) -> Tuple[int, Dict[str, int], Dict[str, List[str]]]:
    raise_csv_field_limit()

    with input_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RuntimeError("The selected CSV file does not have a header row.")
        input_headers = reader.fieldnames
        required_columns = [column for column in SPECIAL_COLUMNS if column not in input_headers]
        if required_columns:
            missing_text = ", ".join(required_columns)
            raise RuntimeError(
                f"The export file is missing required column(s): {missing_text}. "
                "Please export again with the updated OpenMRS exporter."
            )

        first_row = next(reader, None)
        if first_row is None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=SPECIAL_COLUMNS,
                    quoting=csv.QUOTE_ALL,
                )
                writer.writeheader()
            return 0, {MATERNAL_PROGRAM: 0, NEONATAL_PROGRAM: 0, "skipped": 0}, {}

        selected_program = normalize_program_value(first_row.get("program", ""))
        if selected_program not in PROGRAM_SPECS:
            raise RuntimeError(
                "The first data row has an unknown program value: "
                f"{first_row.get('program', '')!r}."
            )
        program_fields = load_program_fields([selected_program])
        resolved_fields, missing_fields = resolve_program_sources(
            program_fields,
            input_headers,
            [selected_program],
        )
        rows_to_write: List[OrderedDict[str, str]] = []
        counts = {MATERNAL_PROGRAM: 0, NEONATAL_PROGRAM: 0, "skipped": 0}
        ordered_target_headers = deduplicate(
            field.target_header for field in program_fields[selected_program]
        )
        if selected_program == MATERNAL_PROGRAM:
            ordered_target_headers = deduplicate(
                tuple(ordered_target_headers) + MATERNAL_COMPUTED_DIAGNOSIS_HEADERS
            )

        for row in chain([first_row], reader):
            program_value = normalize_program_value(row.get("program", ""))
            if program_value not in resolved_fields:
                counts["skipped"] += 1
                continue

            row_org_unit = blank_to_empty(row.get("org_unit", ""))

            transformed_row: "OrderedDict[str, str]" = OrderedDict()
            for column in SPECIAL_COLUMNS:
                transformed_row[column] = blank_to_empty(row.get(column, ""))
            for column in CONTEXT_COLUMNS:
                if column in input_headers:
                    transformed_row[column] = blank_to_empty(row.get(column, ""))
            for target_header in ordered_target_headers:
                transformed_row[target_header] = ""

            for target_header in ordered_target_headers:
                field = select_mapping_field(
                    resolved_fields[program_value],
                    row_org_unit,
                    target_header,
                )
                if not field:
                    continue

                raw_value = row.get(field.source_header, "")
                transformed_row[target_header] = normalize_tracker_value(
                    raw_value=raw_value,
                    data_type=field.data_type,
                    options_text=field.options_text,
                    target_header=field.target_header,
                    program=program_value,
                )

            if program_value == MATERNAL_PROGRAM:
                apply_maternal_diagnosis_transform(transformed_row, row)

            rows_to_write.append(transformed_row)
            counts[program_value] += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=SPECIAL_COLUMNS
            + [column for column in CONTEXT_COLUMNS if any(column in row for row in rows_to_write)]
            + ordered_target_headers,
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        writer.writerows(rows_to_write)

    return len(rows_to_write), counts, missing_fields


class TransformPage(ttk.Frame):
    def __init__(self, parent, on_back_to_menu):
        super().__init__(parent)
        self.parent = parent
        self.on_back_to_menu = on_back_to_menu

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(
            value=str(Path(__file__).resolve().with_name("dhis2_tracker_import.csv"))
        )
        self.mapping_var = tk.StringVar()
        self.dict_var = tk.StringVar()
        self.value_mapping_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value="Select mapping files, choose the OpenMRS export CSV, then transform."
        )
        self.transform_in_progress = False

        self._build_ui()

    def _build_ui(self):
        # Header with Back button
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 18))
        header.pack(fill="x")
        
        back_btn = ttk.Button(header, text="← Back to Main Menu", command=self.go_back)
        back_btn.pack(side="left")
        
        ttk.Label(
            header,
            text="Transform CSV",
            style="Header.TLabel",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="center")
        
        ttk.Label(
            header,
            text="Convert an OpenMRS export CSV into DHIS2 tracker CSV using mapping files.",
            style="Header.TLabel",
            font=("Segoe UI", 10),
        ).pack(anchor="center", pady=(4, 0))

        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(container, text="OpenMRS Export CSV").grid(
            row=row, column=0, sticky="w", pady=4
        )
        input_frame = ttk.Frame(container)
        input_frame.grid(row=row, column=1, sticky="ew", pady=4)
        input_frame.columnconfigure(0, weight=1)
        ttk.Entry(input_frame, textvariable=self.input_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(input_frame, text="Browse", command=self.browse_input).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        ttk.Label(container, text="Mapping Excel File").grid(
            row=row, column=0, sticky="w", pady=4
        )
        mapping_frame = ttk.Frame(container)
        mapping_frame.grid(row=row, column=1, sticky="ew", pady=4)
        mapping_frame.columnconfigure(0, weight=1)
        ttk.Entry(mapping_frame, textvariable=self.mapping_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(mapping_frame, text="Browse", command=self.browse_mapping).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        ttk.Label(container, text="Dictionary Excel File").grid(
            row=row, column=0, sticky="w", pady=4
        )
        dict_frame = ttk.Frame(container)
        dict_frame.grid(row=row, column=1, sticky="ew", pady=4)
        dict_frame.columnconfigure(0, weight=1)
        ttk.Entry(dict_frame, textvariable=self.dict_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(dict_frame, text="Browse", command=self.browse_dictionary).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        ttk.Label(container, text="Value Mapping CSV (Optional)").grid(
            row=row, column=0, sticky="w", pady=4
        )
        value_frame = ttk.Frame(container)
        value_frame.grid(row=row, column=1, sticky="ew", pady=4)
        value_frame.columnconfigure(0, weight=1)
        ttk.Entry(value_frame, textvariable=self.value_mapping_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(value_frame, text="Browse", command=self.browse_value_mapping).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        ttk.Label(container, text="Output CSV").grid(row=row, column=0, sticky="w", pady=4)
        output_frame = ttk.Frame(container)
        output_frame.grid(row=row, column=1, sticky="ew", pady=4)
        output_frame.columnconfigure(0, weight=1)
        ttk.Entry(output_frame, textvariable=self.output_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_frame, text="Browse", command=self.browse_output).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        self.transform_button = ttk.Button(
            container,
            text="Transform CSV",
            command=self.transform_file,
        )
        self.transform_button.grid(row=row, column=1, sticky="w", pady=(10, 12))

        row += 1
        ttk.Label(container, textvariable=self.status_var, foreground="#1f4e79").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        row += 1
        log_frame = ttk.LabelFrame(container, text="Transformation Log", padding=10)
        log_frame.grid(row=row, column=0, columnspan=2, sticky="nsew")
        container.rowconfigure(row, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=20, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def go_back(self):
        self.on_back_to_menu()
        self.destroy()

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_busy(self, busy: bool) -> None:
        self.transform_button.configure(state="disabled" if busy else "normal")

    def browse_input(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose the exported OpenMRS CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.input_var.set(selected)
            input_path = Path(selected)
            if not self.output_var.get().strip():
                self.output_var.set(str(input_path.with_name(f"{input_path.stem}_dhis2.csv")))

    def browse_mapping(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Mapping Excel File (contains both Maternal and Neonatal mappings)",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        )
        if selected:
            self.mapping_var.set(selected)

    def browse_dictionary(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Dictionary Excel File",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        )
        if selected:
            self.dict_var.set(selected)

    def browse_value_mapping(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Value Mapping CSV File (optional)",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.value_mapping_var.set(selected)

    def browse_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="Choose transformed CSV file",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="dhis2_tracker_import.csv",
        )
        if selected:
            self.output_var.set(selected)

    def transform_file(self) -> None:
        if self.transform_in_progress:
            return

        mapping_path = Path(self.mapping_var.get().strip())
        dict_path = Path(self.dict_var.get().strip())
        input_path = Path(self.input_var.get().strip())
        output_path = Path(self.output_var.get().strip())
        value_mapping_path = Path(self.value_mapping_var.get().strip()) if self.value_mapping_var.get().strip() else None

        if not mapping_path.is_file():
            messagebox.showerror("Mapping file required", "Please select a valid mapping Excel file.")
            return
        if not dict_path.is_file():
            messagebox.showerror("Dictionary file required", "Please select a valid dictionary Excel file.")
            return
        if not input_path.is_file():
            messagebox.showerror("Input file required", "Choose a valid OpenMRS export CSV file.")
            return
        if not output_path.name:
            messagebox.showerror("Output file required", "Choose where to save the transformed CSV.")
            return

        # Set the mapping files globally
        set_mapping_files(mapping_path, dict_path, value_mapping_path)
        
        if value_mapping_path and value_mapping_path.exists():
            self.log(f"Loaded value mappings from: {value_mapping_path}")
        elif value_mapping_path:
            self.log(f"Warning: Value mapping file not found at {value_mapping_path}")
        else:
            self.log("No value mapping file provided (optional)")

        def worker() -> None:
            self.transform_in_progress = True
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Transforming CSV for DHIS2 tracker..."))
            try:
                row_count, counts, missing_fields = transform_rows(input_path, output_path)

                def on_success() -> None:
                    self.status_var.set(f"Transformation complete. {row_count} rows written.")
                    self.log(f"Input file: {input_path}")
                    self.log(f"Output file: {output_path}")
                    self.log(f"Maternal rows transformed: {counts[MATERNAL_PROGRAM]}")
                    self.log(f"Neonatal rows transformed: {counts[NEONATAL_PROGRAM]}")
                    if counts["skipped"]:
                        self.log(f"Rows skipped because program was missing or unknown: {counts['skipped']}")
                    for program, missing in missing_fields.items():
                        if not missing:
                            continue
                        self.log(
                            f"{program}: {len(missing)} mapped target fields could not be matched to export columns."
                        )
                    self.set_busy(False)
                    self.transform_in_progress = False
                    messagebox.showinfo(
                        "Transformation complete",
                        f"Transformed {row_count} row(s) into:\n{output_path}",
                    )

                self.after(0, on_success)
            except Exception as exc:
                self.after(0, lambda exc=exc: self._handle_error("Transformation failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_error(self, title: str, exc: Exception) -> None:
        self.status_var.set(f"{title}: {exc}")
        self.log(f"{title}: {exc}")
        self.set_busy(False)
        self.transform_in_progress = False
        messagebox.showerror(title, str(exc))


def run_cli(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Transform an OpenMRS export CSV into a DHIS2 tracker import CSV."
    )
    parser.add_argument("input_csv", type=Path, help="OpenMRS export CSV to transform.")
    parser.add_argument("output_csv", type=Path, help="Destination DHIS2 tracker CSV.")
    parser.add_argument("--mapping", type=Path, required=True, help="Mapping Excel file")
    parser.add_argument("--dictionary", type=Path, required=True, help="Dictionary Excel file")
    parser.add_argument("--value-mapping", type=Path, help="Value mapping CSV file (optional)")
    args = parser.parse_args(argv)

    set_mapping_files(args.mapping, args.dictionary, args.value_mapping)
    row_count, counts, missing_fields = transform_rows(args.input_csv, args.output_csv)
    print(f"Transformation complete. {row_count} row(s) written to {args.output_csv}")
    print(f"Maternal rows transformed: {counts[MATERNAL_PROGRAM]}")
    print(f"Neonatal rows transformed: {counts[NEONATAL_PROGRAM]}")
    if counts["skipped"]:
        print(f"Rows skipped because program was missing or unknown: {counts['skipped']}")
    for program, missing in missing_fields.items():
        if missing:
            print(f"{program}: {len(missing)} mapped target field(s) could not be matched.")
    return 0


def main() -> None:
    if len(sys.argv) > 1:
        raise SystemExit(run_cli(sys.argv[1:]))
    if tk is None:
        raise RuntimeError("Tkinter is not installed. Install python3-tk to use the GUI.")
    root = tk.Tk()
    app = TransformPage(root, lambda: None)
    root.mainloop()


if __name__ == "__main__":
    main()