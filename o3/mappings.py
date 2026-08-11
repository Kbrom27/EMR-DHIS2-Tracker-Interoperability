from __future__ import annotations

import csv
import re
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from config import (
    HEADER_SEPARATOR,
    MATERNAL_PROGRAM,
    NEONATAL_PROGRAM,
    RESOURCES_DIR,
)
from o3.schemas import Form, FormRegistry, load_forms_from_directories
from utils import (
    find_mapping_header,
    is_mapping_source_column,
    normalize_label,
    read_xlsx_rows,
    row_to_dict,
)

DEFAULT_O3_OUTPUT_DIR = RESOURCES_DIR / "O3"

MATERNAL_SCHEMA_DIR = "Maternal Inpatient Data"
NEONATAL_SCHEMA_DIR = "Neonatal Care Form"

DEFAULT_MATERNAL_DICTIONARY = RESOURCES_DIR / "MID data disctionary.xlsx"
DEFAULT_NEONATAL_DICTIONARY = RESOURCES_DIR / "NCF data disctionary.xlsx"

MATERNAL_MAPPING_FILENAME = "EMR-DHIS2 Tracker O3 Maternal Mapping.xlsx"
NEONATAL_MAPPING_FILENAME = "EMR-DHIS2 Tracker O3 Neonatal Mapping.xlsx"
VALUE_MAPPING_FILENAME = "EMR-DHIS2 Tracker O3 Value Mappings.csv"

VALUE_MAPPING_COLUMNS = [
    "program",
    "target_header",
    "stage_name",
    "data_element_name",
    "source_value",
    "dhis2_value",
    "transform",
    "notes",
    "org_unit",
    "source_concept_name",
    "source_concept_uuid",
]

MAPPING_HEADERS = [
    "DHIS2 Program Stage Name",
    "EMR Concept Name",
    "DHIS2 Data Element Name",
]

O3_TEA_SOURCE_COLUMNS = {
    "mother first name": "first_name",
    "mother father name": "family_name",
    "mother grandfather name": "family_name",
    "age": "age",
    "education level": "education_details",
    "occupation": "occupation",
    "mother phone number": "primary_contact",
    "mrn": "patient_id",
    "mother adress": "address1",
    "neonate first name": "first_name",
    "neonate last name": "family_name",
    "neonate mrn": "patient_id",
    "neonate sex": "gender",
}


def escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def write_xlsx_rows(path: Path, rows: Sequence[Sequence[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    sheet_rows: List[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row):
            column_letter = _column_letter(column_index)
            cell_ref = f"{column_letter}{row_index}"
            text = escape_xml(str(value or ""))
            cells.append(
                f'<c r="{cell_ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Mapping" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )

    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _column_letter(index: int) -> str:
    letters = ""
    index = index + 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def load_dictionary_fields(path: Path) -> Dict[str, Dict[str, Dict[str, str]]]:
    rows = read_xlsx_rows(path)
    if not rows:
        raise RuntimeError(f"No rows were found in {path.name}.")
    headers = rows[0]
    stages: Dict[str, Dict[str, Dict[str, str]]] = {}
    for row in rows[1:]:
        item = row_to_dict(row, headers)
        stage_name = str(item.get("Stage Name", "")).strip()
        data_element_name = str(item.get("Data Element Name", "")).strip()
        if not stage_name or not data_element_name:
            continue
        stages.setdefault(stage_name, {})[data_element_name] = {
            "data_element_id": str(item.get("Data Element ID", "")).strip(),
            "form_name": str(item.get("Form Name", "")).strip(),
            "data_type": str(item.get("Data Type", "")).strip(),
            "options_text": str(item.get("Options", "")).strip(),
        }
    return stages


def match_stage_name(form_name: str, dictionary_stages: Dict[str, Dict[str, Dict[str, str]]]) -> str:
    normalized_form = normalize_label(form_name)
    if not normalized_form:
        return ""

    for stage_name in dictionary_stages:
        if normalize_label(stage_name) == normalized_form:
            return stage_name

    best_stage = ""
    best_score = 0.0
    for stage_name in dictionary_stages:
        score = SequenceMatcher(None, normalized_form, normalize_label(stage_name)).ratio()
        if score > best_score:
            best_score = score
            best_stage = stage_name
    return best_stage if best_score >= 0.9 else ""


def match_data_element(
    question_label: str,
    stage_elements: Dict[str, Dict[str, str]],
) -> Tuple[str, Dict[str, str]]:
    normalized_label_text = normalize_label(question_label)
    if not normalized_label_text:
        return "", {}

    exact_matches = [
        name
        for name in stage_elements
        if normalize_label(name) == normalized_label_text
    ]
    if len(exact_matches) == 1:
        return exact_matches[0], stage_elements[exact_matches[0]]

    base_label = normalized_label_text.rstrip(":")
    if base_label != normalized_label_text:
        exact_matches = [name for name in stage_elements if normalize_label(name) == base_label]
        if len(exact_matches) == 1:
            return exact_matches[0], stage_elements[exact_matches[0]]

    best_name = ""
    best_field = {}
    best_score = 0.0
    for name, field in stage_elements.items():
        score = SequenceMatcher(None, normalized_label_text, normalize_label(name)).ratio()
        if score > best_score:
            best_score = score
            best_name = name
            best_field = field
    if best_score >= 0.88:
        return best_name, best_field
    return "", {}


def parse_ordered_options(options_text: str) -> List[Tuple[str, str]]:
    options: List[Tuple[str, str]] = []
    for raw_option in str(options_text or "").split(";"):
        option = raw_option.strip()
        if not option:
            continue
        if ":" in option:
            code, label = option.split(":", 1)
        else:
            code = option
            label = option
        code = code.strip()
        label = label.strip()
        if not code and not label:
            continue
        options.append((label or code, code))
    return options


def build_program_rows(
    forms: List[Form],
    dictionary_path: Path,
    program_value: str,
) -> Tuple[List[List[str]], List[Dict[str, str]]]:
    dictionary_stages = load_dictionary_fields(dictionary_path)

    mapping_rows: List[List[str]] = []
    value_rows: List[Dict[str, str]] = []
    seen_mappings = set()
    seen_values = set()

    for form in forms:
        stage_name = match_stage_name(form.name, dictionary_stages)
        if not stage_name:
            continue
        stage_elements = dictionary_stages[stage_name]

        for question in form.questions:
            if not question.label:
                continue
            data_element_name, data_element_field = match_data_element(
                question.label, stage_elements
            )
            if not data_element_name:
                continue

            source_name = f"{question.label} [{form.name}]"
            mapping_key = (stage_name, source_name, data_element_name)
            if mapping_key not in seen_mappings:
                seen_mappings.add(mapping_key)
                mapping_rows.append([stage_name, source_name, data_element_name])

            options_text = data_element_field.get("options_text", "")
            if not options_text or not question.answers:
                continue
            dhis2_options = parse_ordered_options(options_text)

            for answer in question.answers:
                if not answer.label or not answer.concept:
                    continue
                matched_option = _match_dhis2_option(answer.label, dhis2_options)
                if matched_option is None:
                    continue
                target_header = f"{stage_name}{HEADER_SEPARATOR}{data_element_name}"
                value_key = (program_value, target_header, answer.concept, answer.label)
                if value_key in seen_values:
                    continue
                seen_values.add(value_key)
                value_rows.append(
                    {
                        "program": program_value,
                        "target_header": target_header,
                        "stage_name": stage_name,
                        "data_element_name": data_element_name,
                        "source_value": answer.label,
                        "dhis2_value": matched_option,
                        "transform": "",
                        "notes": "",
                        "org_unit": "",
                        "source_concept_name": question.label,
                        "source_concept_uuid": question.concept,
                    }
                )

    tea_rows = build_tea_rows(dictionary_stages)
    for tea_row in tea_rows:
        mapping_key = ("Tracked Entity Attributes", tea_row[1], tea_row[2])
        if mapping_key not in seen_mappings:
            seen_mappings.add(mapping_key)
            mapping_rows.append(tea_row)

    mapping_rows.sort(key=lambda row: (row[0].casefold(), row[1].casefold()))
    value_rows.sort(
        key=lambda row: (
            row["stage_name"].casefold(),
            row["data_element_name"].casefold(),
            row["source_value"].casefold(),
        )
    )
    return mapping_rows, value_rows


def build_tea_rows(
    dictionary_stages: Dict[str, Dict[str, Dict[str, str]]],
) -> List[List[str]]:
    tea_stage = dictionary_stages.get("Tracked Entity Attributes", {})
    tea_rows: List[List[str]] = []
    if not tea_stage:
        return tea_rows
    for data_element_name, _field in tea_stage.items():
        if data_element_name.casefold().endswith("record id"):
            continue
        source_column = O3_TEA_SOURCE_COLUMNS.get(normalize_label(data_element_name))
        if not source_column:
            continue
        tea_rows.append(["Tracked Entity Attributes", source_column, data_element_name])
    return tea_rows


def _match_dhis2_option(answer_label: str, dhis2_options: Sequence[Tuple[str, str]]) -> Optional[str]:
    normalized = normalize_label(answer_label)
    if not normalized:
        return None
    for label, _code in dhis2_options:
        if normalize_label(label) == normalized:
            return label
    for label, _code in dhis2_options:
        if normalize_label(label).rstrip(":") == normalized.rstrip(":"):
            return label
    best_label = ""
    best_score = 0.0
    for label, _code in dhis2_options:
        score = SequenceMatcher(None, normalized, normalize_label(label)).ratio()
        if score > best_score:
            best_score = score
            best_label = label
    return best_label if best_score >= 0.9 else None


def load_forms_for_program(schema_root: Path, subdir_name: str) -> List[Form]:
    directory = schema_root / subdir_name
    if not directory.is_dir():
        return []
    return load_forms_from_directories([directory]).forms


def generate_o3_mappings(
    schema_root: Path,
    maternal_dictionary: Path,
    neonatal_dictionary: Path,
    output_dir: Path,
) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)

    maternal_forms = load_forms_for_program(schema_root, MATERNAL_SCHEMA_DIR)
    neonatal_forms = load_forms_for_program(schema_root, NEONATAL_SCHEMA_DIR)

    if not maternal_forms and not neonatal_forms:
        raise RuntimeError(
            f"No OpenMRS 3 form schemas were found under {schema_root}. "
            "Select the folder that contains 'Maternal Inpatient Data' and "
            "'Neonatal Care Form' subfolders with .json schemas."
        )

    maternal_mapping_path = output_dir / MATERNAL_MAPPING_FILENAME
    neonatal_mapping_path = output_dir / NEONATAL_MAPPING_FILENAME
    value_mapping_path = output_dir / VALUE_MAPPING_FILENAME

    maternal_mapping_rows: List[List[str]] = []
    maternal_value_rows: List[Dict[str, str]] = []
    if maternal_forms:
        maternal_mapping_rows, maternal_value_rows = build_program_rows(
            maternal_forms, maternal_dictionary, MATERNAL_PROGRAM
        )

    neonatal_mapping_rows: List[List[str]] = []
    neonatal_value_rows: List[Dict[str, str]] = []
    if neonatal_forms:
        neonatal_mapping_rows, neonatal_value_rows = build_program_rows(
            neonatal_forms, neonatal_dictionary, NEONATAL_PROGRAM
        )

    if maternal_mapping_rows:
        write_xlsx_rows(
            maternal_mapping_path,
            [MAPPING_HEADERS] + maternal_mapping_rows,
        )
    if neonatal_mapping_rows:
        write_xlsx_rows(
            neonatal_mapping_path,
            [MAPPING_HEADERS] + neonatal_mapping_rows,
        )

    all_value_rows = maternal_value_rows + neonatal_value_rows
    with value_mapping_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=VALUE_MAPPING_COLUMNS,
            extrasaction="ignore",
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        writer.writerows(all_value_rows)

    return {
        "maternal_forms": len(maternal_forms),
        "neonatal_forms": len(neonatal_forms),
        "maternal_mapping_rows": len(maternal_mapping_rows),
        "neonatal_mapping_rows": len(neonatal_mapping_rows),
        "value_mapping_rows": len(all_value_rows),
        "maternal_mapping_path": str(maternal_mapping_path),
        "neonatal_mapping_path": str(neonatal_mapping_path),
        "value_mapping_path": str(value_mapping_path),
    }
