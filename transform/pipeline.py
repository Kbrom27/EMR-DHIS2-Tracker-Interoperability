from __future__ import annotations

import csv
from collections import OrderedDict
from itertools import chain
from pathlib import Path
from typing import Dict, List, Sequence

from config import (
    CONTEXT_COLUMNS,
    MATERNAL_COMPUTED_DIAGNOSIS_HEADERS,
    MATERNAL_DIAGNOSIS_SOURCE_HEADERS,
    MATERNAL_PROGRAM,
    NEONATAL_PROGRAM,
    PROGRAM_SPECS,
    SPECIAL_COLUMNS,
    is_patient_eligible_for_program,
)
from models import MappingField
from transform.investigations import (
    NEONATAL_INVESTIGATION_HEADERS,
    apply_neonatal_investigation_transform,
)
from transform.mapping import load_program_fields
from transform.matcher import resolve_program_sources, select_mapping_field
from transform.normalizers import normalize_tracker_value
from utils import (
    blank_to_empty,
    deduplicate,
    normalize_label,
    raise_csv_field_limit,
)


DIAGNOSIS_METADATA_VALUES = {
    "primary", "secondary", "confirmed", "presumed", "false", "true",
}
UUID_PATTERN = __import__("re").compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    __import__("re").IGNORECASE,
)
DIAGNOSIS_OBSTETRIC_COMPLICATIONS_HEADER = "Diagnosis :: Obstetric complications"
DIAGNOSIS_AMNIOTIC_FLUID_HEADER = "Diagnosis :: Amniotic fluid abnormalities"
DIAGNOSIS_OBSTETRIC_COMPLICATIONS_OTHER_HEADER = "Diagnosis :: Obstetric complications Others"


def is_diagnosis_metadata_value(value: str) -> bool:
    import re
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


def map_maternal_diagnosis_values(raw_value: str) -> tuple[str, str, str]:
    import re

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


def transform_rows(
    input_path: Path,
    output_path: Path,
) -> tuple[int, Dict[str, int], Dict[str, List[str]]]:
    from config import normalize_program_value

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
        elif selected_program == NEONATAL_PROGRAM:
            ordered_target_headers = deduplicate(
                tuple(ordered_target_headers) + tuple(NEONATAL_INVESTIGATION_HEADERS)
            )

        for row in chain([first_row], reader):
            program_value = normalize_program_value(row.get("program", ""))
            gender = row.get("gender", "")
            age = row.get("age")
            if not is_patient_eligible_for_program(gender, age, program_value):
                counts["skipped"] += 1
                continue
            if program_value not in resolved_fields:
                counts["skipped"] += 1
                continue

            row_org_unit = blank_to_empty(row.get("org_unit", ""))

            transformed_row: OrderedDict[str, str] = OrderedDict()
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
            elif program_value == NEONATAL_PROGRAM:
                apply_neonatal_investigation_transform(transformed_row, row)

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
