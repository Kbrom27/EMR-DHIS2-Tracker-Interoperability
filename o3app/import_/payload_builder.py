from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from o3app.clients.dhis2_client import (
    EVENT_DATE_HINTS,
    add_import_value_issue,
    invalid_value_reason,
    normalize_boolean_token,
    normalize_datetime_value,
    normalize_numeric_value,
    normalize_time_value,
    today_date,
)
from o3app.config import BLANK_MARKERS, HEADER_SEPARATOR, RESOURCES_DIR, normalize_stage_name
from o3app.models import AttributeField, ImportValueIssue, ProgramConfig, StageField
from o3app.rules.tracker_mapping_rules import (
    apply_field_alias,
    get_field_transform,
    resolve_configured_option_value,
    should_suppress_value,
)
from o3app.utils import blank_to_empty, normalize_date, normalize_label, read_xlsx_rows, row_to_dict


METADATA_PATH = RESOURCES_DIR / "metadata.json"
LEGACY_METADATA_PATH = RESOURCES_DIR / "Old" / "metadata.json"


def load_metadata(path: Path) -> Dict:
    resolved_path = path
    if not resolved_path.exists() and path == METADATA_PATH and LEGACY_METADATA_PATH.exists():
        resolved_path = LEGACY_METADATA_PATH
    if not resolved_path.exists():
        raise FileNotFoundError(f"Required metadata file not found: {path}")
    with resolved_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_dictionary_rows(path: Path) -> List[Dict[str, str]]:
    rows = read_xlsx_rows(path)
    if not rows:
        raise RuntimeError(f"No rows were found in {path.name}.")
    headers = rows[0]
    return [row_to_dict(row, headers) for row in rows[1:]]


def build_program_configs() -> Dict[str, ProgramConfig]:
    from collections import defaultdict

    from config import PROGRAM_SPECS

    metadata = load_metadata(METADATA_PATH)
    programs_by_id = {item["id"]: item for item in metadata.get("programs", [])}
    stages_by_program: Dict[str, Dict[str, str]] = defaultdict(dict)

    for stage in metadata.get("programStages", []):
        program = stage.get("program") or {}
        program_id = str(program.get("id") or "").strip()
        stage_name = normalize_stage_name(str(stage.get("name") or "").strip())
        stage_id = str(stage.get("id") or "").strip()
        if program_id and stage_name and stage_id:
            stages_by_program[program_id][stage_name] = stage_id

    configs: Dict[str, ProgramConfig] = {}
    for program_label, spec in PROGRAM_SPECS.items():
        program_uid = program_label.split("/")[-1]
        program_meta = programs_by_id.get(program_uid)
        if not program_meta:
            raise RuntimeError(f"Program metadata for {program_uid} was not found in metadata.json.")

        tracked_entity_type = str(program_meta.get("trackedEntityType", {}).get("id") or "").strip()
        dictionary_rows = read_dictionary_rows(spec["dictionary_path"])
        attributes: Dict[str, AttributeField] = {}
        stages: Dict[str, List[StageField]] = defaultdict(list)
        record_id_attribute_id = ""

        for item in dictionary_rows:
            stage_name = normalize_stage_name(str(item.get("Stage Name", "")).strip())
            data_element_name = str(item.get("Data Element Name", "")).strip()
            data_element_id = str(item.get("Data Element ID", "")).strip()
            data_type = str(item.get("Data Type", "")).strip()
            options_text = str(item.get("Options", "")).strip()

            if not stage_name or not data_element_name or not data_element_id:
                continue

            header = f"{stage_name}{HEADER_SEPARATOR}{data_element_name}"
            if stage_name == "Tracked Entity Attributes":
                attributes[header] = AttributeField(
                    header=header,
                    attribute_id=data_element_id,
                    attribute_name=data_element_name,
                    data_type=data_type,
                    options_text=options_text,
                )
                if data_element_name.casefold().endswith("record id"):
                    record_id_attribute_id = data_element_id
                continue

            stage_id = stages_by_program[program_uid].get(stage_name)
            if not stage_id:
                continue
            stages[stage_name].append(
                StageField(
                    header=header,
                    stage_name=stage_name,
                    stage_id=stage_id,
                    data_element_id=data_element_id,
                    data_element_name=data_element_name,
                    data_type=data_type,
                    options_text=options_text,
                )
            )

        if not tracked_entity_type:
            raise RuntimeError(f"Tracked entity type is missing for program {program_uid}.")
        if not record_id_attribute_id:
            raise RuntimeError(f"Record ID attribute could not be found for program {program_uid}.")

        configs[program_label] = ProgramConfig(
            program_label=program_label,
            program_uid=program_uid,
            tracked_entity_type=tracked_entity_type,
            record_id_attribute_id=record_id_attribute_id,
            attributes=attributes,
            stages=dict(stages),
        )

    return configs


def extract_row_value(row: Dict[str, str], header: str) -> str:
    return str(row.get(header, "") or "").strip()


def parse_option_codes(
    options_text: str,
) -> tuple[Dict[str, str], Dict[str, str], List[tuple[tuple[str, ...], str]]]:
    code_map: Dict[str, str] = {}
    label_map: Dict[str, str] = {}
    token_map: List[tuple[tuple[str, ...], str]] = []

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
        if not code and not label:
            continue
        canonical_code = code or label
        canonical_label = label or code
        if code:
            code_map[code.casefold()] = canonical_code
        label_map[normalize_label(canonical_code)] = canonical_code
        label_map[normalize_label(canonical_label)] = canonical_code
        token_map.append((option_tokens(canonical_label), canonical_code))

    return code_map, label_map, token_map


def option_tokens(value: str) -> tuple[str, ...]:
    normalized = normalize_label(value)
    if not normalized:
        return ()
    return tuple(sorted(set(normalized.split())))


def split_option_parts(value: str, multi_value: bool) -> List[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if not multi_value:
        return [text]
    parts = re.split(r"\s*[|;,]\s*", text)
    return [part.strip() for part in parts if part.strip()]


def resolve_option_code(
    part: str,
    code_map: Dict[str, str],
    label_map: Dict[str, str],
    token_map: Sequence[tuple[tuple[str, ...], str]],
) -> str:
    from difflib import SequenceMatcher

    exact_code = code_map.get(part.casefold())
    if exact_code:
        return exact_code

    normalized = normalize_label(part)
    mapped = label_map.get(normalized)
    if mapped:
        return mapped

    boolean_value = normalize_boolean_token(part)
    if boolean_value == "true" and "1" in code_map:
        return code_map["1"]
    if boolean_value == "false" and "0" in code_map:
        return code_map["0"]

    part_token_set = set(option_tokens(part))
    best_code = ""
    best_score = 0.0
    for option_token_tuple, option_code in token_map:
        option_token_set = set(option_token_tuple)
        if not option_token_set:
            continue
        if part_token_set and option_token_set.issubset(part_token_set):
            score = 0.9 + (len(option_token_set) / max(len(part_token_set), 1)) * 0.05
        else:
            score = SequenceMatcher(None, normalized, normalize_label(" ".join(option_token_tuple))).ratio()
        if score > best_score:
            best_score = score
            best_code = option_code

    return best_code if best_score >= 0.84 else ""


def normalize_import_option_value(
    value: str,
    data_type: str,
    options_text: str,
    target_header: str,
    discarded_parts: Optional[List[str]] = None,
) -> str:
    code_map, label_map, token_map = parse_option_codes(options_text)
    multi_value = data_type == "MULTI_TEXT"
    parts = split_option_parts(value, multi_value=multi_value)
    if not parts:
        return ""

    resolved_values: List[str] = []
    for part in parts:
        configured = resolve_configured_option_value(
            raw_value=part,
            options_text=options_text,
            target_header=target_header,
            return_codes=True,
        )
        if configured:
            resolved_values.append(configured)
            continue

        resolved = resolve_option_code(part, code_map, label_map, token_map)
        if resolved:
            resolved_values.append(resolved)
        elif discarded_parts is not None:
            discarded_parts.append(part.strip())
        else:
            resolved_values.append(part.strip())

    deduped: List[str] = []
    for item in resolved_values:
        if item and item not in deduped:
            deduped.append(item)

    if not deduped:
        return ""
    return ",".join(deduped) if multi_value else deduped[-1]


def _option_value_present(value: str, options_text: str) -> bool:
    if not options_text:
        return False
    code_map, label_map, _ = parse_option_codes(options_text)
    normalized = normalize_label(value)
    return value.casefold() in code_map or normalized in label_map


def normalize_import_value(
    value: str,
    data_type: str,
    options_text: str = "",
    target_header: str = "",
    discarded_parts: Optional[List[str]] = None,
) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.casefold() in BLANK_MARKERS and not _option_value_present(text, options_text):
        return ""
    if should_suppress_value(text, target_header):
        return ""

    configured_transform = get_field_transform(target_header)
    if configured_transform == "date":
        normalized = normalize_date(text)
        return normalized if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized) else ""

    if configured_transform == "time":
        return normalize_time_value(text)

    if configured_transform == "datetime":
        return normalize_datetime_value(text)

    if options_text:
        return normalize_import_option_value(
            text,
            data_type,
            options_text,
            target_header,
            discarded_parts=discarded_parts,
        )

    if data_type == "DATE":
        normalized = normalize_date(text)
        return normalized if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized) else ""

    if data_type == "TIME":
        return normalize_time_value(text)

    if data_type == "DATETIME":
        return normalize_datetime_value(text)

    if data_type in {"INTEGER", "INTEGER_ZERO_OR_POSITIVE", "INTEGER_POSITIVE", "INTEGER_NEGATIVE"}:
        return normalize_numeric_value(text, integer_only=True)

    if data_type in {"NUMBER", "PERCENTAGE", "UNIT_INTERVAL"}:
        return normalize_numeric_value(text, integer_only=False)

    configured_text = apply_field_alias(text, target_header)
    return configured_text or text


def infer_stage_date(stage_fields: Sequence[StageField], row: Dict[str, str], fallback: str) -> str:
    preferred: List[str] = []
    generic: List[str] = []

    for field in stage_fields:
        if field.data_type not in {"DATE", "DATETIME"}:
            continue
        value = extract_row_value(row, field.header)
        if not value:
            continue
        date_value = normalize_date(value)
        field_name = field.data_element_name.casefold()
        if any(hint in field_name for hint in EVENT_DATE_HINTS):
            preferred.append(date_value)
        elif "lnmp" not in field_name and "edd" not in field_name:
            generic.append(date_value)

    if preferred:
        return preferred[-1]
    if generic:
        return generic[-1]
    return fallback


def infer_enrollment_date(config: ProgramConfig, row: Dict[str, str]) -> str:
    import re

    visit_date = normalize_date(extract_row_value(row, "visit_date"))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", visit_date):
        return visit_date

    dates: List[str] = []
    for stage_fields in config.stages.values():
        stage_date = infer_stage_date(stage_fields, row, "")
        if stage_date:
            dates.append(stage_date)
    return min(dates) if dates else today_date()


def build_attribute_payload(
    config: ProgramConfig,
    row: Dict[str, str],
    issues: Optional[List[ImportValueIssue]] = None,
) -> List[Dict[str, str]]:
    values_by_attribute: Dict[str, str] = {
        config.record_id_attribute_id: extract_row_value(row, "Record ID")
    }

    for header, field in config.attributes.items():
        raw_value = extract_row_value(row, header)
        discarded_parts: List[str] = []
        value = normalize_import_value(
            raw_value,
            field.data_type,
            field.options_text,
            header,
            discarded_parts=discarded_parts,
        )
        for discarded in discarded_parts:
            add_import_value_issue(
                issues,
                row,
                config,
                "Tracked Entity Attributes",
                header,
                field.attribute_name,
                field.attribute_id,
                discarded,
                invalid_value_reason(field.data_type, field.options_text),
            )
        if raw_value and not value and not discarded_parts:
            add_import_value_issue(
                issues,
                row,
                config,
                "Tracked Entity Attributes",
                header,
                field.attribute_name,
                field.attribute_id,
                raw_value,
                invalid_value_reason(field.data_type, field.options_text),
            )
        if not value:
            continue
        values_by_attribute[field.attribute_id] = value

    return [
        {"attribute": attribute_id, "value": value}
        for attribute_id, value in values_by_attribute.items()
        if value
    ]


def build_stage_payloads(
    config: ProgramConfig,
    row: Dict[str, str],
    default_date: str,
    issues: Optional[List[ImportValueIssue]] = None,
) -> List[Dict[str, object]]:
    payloads: List[Dict[str, object]] = []
    for stage_name, stage_fields in config.stages.items():
        data_values = []
        data_value_fields = {}
        for field in stage_fields:
            raw_value = extract_row_value(row, field.header)
            discarded_parts: List[str] = []
            value = normalize_import_value(
                raw_value,
                field.data_type,
                field.options_text,
                field.header,
                discarded_parts=discarded_parts,
            )
            for discarded in discarded_parts:
                add_import_value_issue(
                    issues,
                    row,
                    config,
                    stage_name,
                    field.header,
                    field.data_element_name,
                    field.data_element_id,
                    discarded,
                    invalid_value_reason(field.data_type, field.options_text),
                )
            if raw_value and not value and not discarded_parts:
                add_import_value_issue(
                    issues,
                    row,
                    config,
                    stage_name,
                    field.header,
                    field.data_element_name,
                    field.data_element_id,
                    raw_value,
                    invalid_value_reason(field.data_type, field.options_text),
                )
            if not value:
                continue
            data_values.append({"dataElement": field.data_element_id, "value": value})
            data_value_fields[field.data_element_id] = {
                "column": field.header,
                "field_name": field.data_element_name,
            }

        if not data_values:
            continue

        payloads.append(
            {
                "stage_name": stage_name,
                "programStage": stage_fields[0].stage_id,
                "eventDate": default_date,
                "dataValues": data_values,
                "data_value_fields": data_value_fields,
            }
        )
    return payloads
