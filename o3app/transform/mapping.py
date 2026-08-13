from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from o3app.config import (
    HEADER_SEPARATOR,
    MATERNAL_PROGRAM,
    NEONATAL_PROGRAM,
    PROGRAM_SPECS,
    RESOURCES_DIR,
    SPECIAL_COLUMNS,
    normalize_stage_name,
)
from o3app.models import DictionaryField, MappingField
from o3app.rules.tracker_mapping_rules import set_value_mapping_path
from o3app.utils import (
    blank_to_empty,
    extract_bracket_label,
    find_mapping_header,
    is_mapping_source_column,
    normalize_label,
    normalized_tokens,
    read_xlsx_rows,
    row_to_dict,
)


TARGETED_DICTIONARY_STAGE_FALLBACKS = {
    MATERNAL_PROGRAM: {"Laboratory", "Physicians Medication Order"},
    NEONATAL_PROGRAM: set(),
}

AGGREGATE_SOURCE_OVERRIDES = {
    (MATERNAL_PROGRAM, "Laboratory", "Laboratory event date"): "visit_date",
    (MATERNAL_PROGRAM, "Laboratory", "Other Laboratory Investigations"): "lab_results",
    (MATERNAL_PROGRAM, "Physicians Medication Order", "Physician Medication order event date"): "visit_date",
    (MATERNAL_PROGRAM, "Physicians Medication Order", "Medication order date"): "visit_date",
    (MATERNAL_PROGRAM, "Physicians Medication Order", "Ordered medication name"): "medications",
}

FIELD_SOURCE_ALIASES = {
    "neonate first name": "first_name",
    "neonate last name": "family_name",
    "neonate mrn": "patient_id",
    "neonate sex": "gender",
}

_user_mapping_excel_path: Optional[Path] = None
_user_dictionary_excel_path: Optional[Path] = None
_user_value_mapping_csv_path: Optional[Path] = None


def set_mapping_files(mapping_excel_path: Path, dictionary_excel_path: Path, value_mapping_csv_path: Optional[Path] = None) -> None:
    global _user_mapping_excel_path, _user_dictionary_excel_path, _user_value_mapping_csv_path

    _user_mapping_excel_path = mapping_excel_path
    _user_dictionary_excel_path = dictionary_excel_path
    _user_value_mapping_csv_path = value_mapping_csv_path

    from config import PROGRAM_SPECS as _ps
    _ps[MATERNAL_PROGRAM] = {
        "mapping_path": mapping_excel_path,
        "dictionary_path": dictionary_excel_path,
    }
    _ps[NEONATAL_PROGRAM] = {
        "mapping_path": mapping_excel_path,
        "dictionary_path": dictionary_excel_path,
    }

    if value_mapping_csv_path:
        set_value_mapping_path(value_mapping_csv_path)


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


def read_dictionary_fields(path: Path) -> Dict[Tuple[str, str], DictionaryField]:
    rows = read_xlsx_rows(path)
    if not rows:
        raise RuntimeError(f"No rows were found in {path.name}.")

    headers = rows[0]
    fields: Dict[Tuple[str, str], DictionaryField] = {}
    for row in rows[1:]:
        item = row_to_dict(row, headers)
        stage_name = normalize_stage_name(item.get("Stage Name", "").strip())
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


def load_program_fields(programs: Optional[Sequence[str]] = None) -> Dict[str, List[MappingField]]:
    from typing import Sequence

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
