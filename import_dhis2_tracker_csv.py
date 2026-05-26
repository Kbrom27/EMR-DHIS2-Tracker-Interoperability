import csv
import json
import re
import threading
import tkinter as tk
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Sequence, Tuple

import requests
import urllib3

from tracker_mapping_rules import (
    apply_field_alias,
    get_field_transform,
    resolve_configured_option_value,
    should_suppress_value,
)
from transform_export_to_dhis2_csv import (
    BLANK_MARKERS,
    HEADER_SEPARATOR,
    MATERNAL_PROGRAM,
    NEONATAL_PROGRAM,
    PROGRAM_SPECS,
    SPECIAL_COLUMNS,
    blank_to_empty,
    normalize_date,
    normalize_program_value,
    raise_csv_field_limit,
    read_xlsx_rows,
    row_to_dict,
)


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


RESOURCES_DIR = Path(__file__).resolve().with_name("Resources")
METADATA_PATH = RESOURCES_DIR / "metadata.json"
LEGACY_METADATA_PATH = RESOURCES_DIR / "Old" / "metadata.json"
EVENT_DATE_HINTS = (
    "event date",
    "admission date",
    "date and time of admission",
    "date of delivery",
    "date of birth",
    "evaluation date",
    "date of referral",
    "discharge date",
    "date of visit",
    "date form filled",
)


@dataclass
class AttributeField:
    header: str
    attribute_id: str
    attribute_name: str
    data_type: str
    options_text: str


@dataclass
class StageField:
    header: str
    stage_name: str
    stage_id: str
    data_element_id: str
    data_element_name: str
    data_type: str
    options_text: str


@dataclass
class ProgramConfig:
    program_label: str
    program_uid: str
    tracked_entity_type: str
    record_id_attribute_id: str
    attributes: Dict[str, AttributeField]
    stages: Dict[str, List[StageField]]


@dataclass
class ImportValueIssue:
    record_id: str
    patient: str
    program: str
    stage: str
    column: str
    field_name: str
    field_id: str
    value: str
    reason: str


class Dhis2RequestError(RuntimeError):
    def __init__(self, method: str, url: str, status_code: int, payload: object) -> None:
        self.method = method
        self.url = url
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"{method} {url} failed: {payload}")


def normalize_dhis2_base_url(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    if not value:
        raise ValueError("DHIS2 URL is required.")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    lower = value.lower()
    if lower.endswith("/api"):
        return value
    if "/api/" in lower:
        return value.rstrip("/")
    return f"{value}/api"


def today_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


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
    metadata = load_metadata(METADATA_PATH)
    programs_by_id = {item["id"]: item for item in metadata.get("programs", [])}
    stages_by_program: Dict[str, Dict[str, str]] = defaultdict(dict)

    for stage in metadata.get("programStages", []):
        program = stage.get("program") or {}
        program_id = str(program.get("id") or "").strip()
        stage_name = str(stage.get("name") or "").strip()
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
            stage_name = str(item.get("Stage Name", "")).strip()
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
    return blank_to_empty(row.get(header, ""))


def looks_like_uid(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]{10}", value or ""))


def reference_id(value: object) -> str:
    if isinstance(value, dict):
        for key in ("id", "uid", "programStage", "program", "trackedEntityInstance", "enrollment"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
        return ""
    return str(value or "").strip()


def normalize_time_value(value: str) -> str:
    match = re.search(r"(?:T|\b)(\d{1,2}:\d{2})(?::\d{2})?", str(value or ""))
    if not match:
        return ""
    hour, minute = match.group(1).split(":")
    return f"{int(hour):02d}:{minute}"


def normalize_datetime_value(value: str) -> str:
    date_value = normalize_date(value)
    time_value = normalize_time_value(value)
    if not date_value or not time_value:
        return ""
    return f"{date_value}T{time_value}:00"


def normalize_numeric_value(value: str, integer_only: bool) -> str:
    cleaned = str(value or "").strip().replace(",", "")
    if not cleaned:
        return ""
    if integer_only:
        fallback = re.sub(r"[^0-9+-]", "", cleaned)
        if not fallback or fallback in {"+", "-"}:
            return ""
        return fallback
    fallback = re.sub(r"[^0-9+-.]", "", cleaned)
    if not fallback or fallback in {"+", "-", ".", "+.", "-."}:
        return ""
    return fallback


def normalize_label(value: str) -> str:
    cleaned = (
        str(value or "")
        .replace("’", "'")
        .replace("`", "'")
        .replace("“", '"')
        .replace("”", '"')
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


def option_tokens(value: str) -> Tuple[str, ...]:
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
    parts = re.split(r"\s*\|\s*|;", text)
    return [part.strip() for part in parts if part.strip()]


def parse_option_codes(
    options_text: str,
) -> Tuple[Dict[str, str], Dict[str, str], List[Tuple[Tuple[str, ...], str]]]:
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


def normalize_boolean_token(value: str) -> Optional[str]:
    normalized = normalize_label(value)
    if normalized in {"1", "true", "t", "yes", "y"}:
        return "true"
    if normalized in {"0", "false", "f", "no", "n"}:
        return "false"
    return None


def resolve_option_code(
    part: str,
    code_map: Dict[str, str],
    label_map: Dict[str, str],
    token_map: Sequence[Tuple[Tuple[str, ...], str]],
) -> str:
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
    return ";".join(deduped) if multi_value else deduped[-1]


def normalize_import_value(
    value: str,
    data_type: str,
    options_text: str = "",
    target_header: str = "",
    discarded_parts: Optional[List[str]] = None,
) -> str:
    text = blank_to_empty(value)
    if not text:
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
    dates: List[str] = []
    for stage_fields in config.stages.values():
        stage_date = infer_stage_date(stage_fields, row, "")
        if stage_date:
            dates.append(stage_date)
    return min(dates) if dates else today_date()


def patient_label(row: Dict[str, str]) -> str:
    candidates = [
        "Patient",
        "patient",
        "Patient Name",
        "Full Name",
        "Name",
        "MRN",
        "Record ID",
    ]
    for column in candidates:
        value = extract_row_value(row, column)
        if value:
            return value
    return ""


def add_import_value_issue(
    issues: Optional[List[ImportValueIssue]],
    row: Dict[str, str],
    config: ProgramConfig,
    stage: str,
    column: str,
    field_name: str,
    field_id: str,
    value: str,
    reason: str,
) -> None:
    if issues is None or not blank_to_empty(value):
        return
    issues.append(
        ImportValueIssue(
            record_id=extract_row_value(row, "Record ID"),
            patient=patient_label(row),
            program=config.program_label,
            stage=stage,
            column=column,
            field_name=field_name,
            field_id=field_id,
            value=blank_to_empty(value),
            reason=reason,
        )
    )


def invalid_value_reason(data_type: str, options_text: str) -> str:
    if options_text:
        return "Value is not a configured DHIS2 option and was discarded."
    if data_type in {"DATE", "TIME", "DATETIME"}:
        return f"Value could not be converted to DHIS2 {data_type} format and was discarded."
    if data_type in {
        "INTEGER",
        "INTEGER_ZERO_OR_POSITIVE",
        "INTEGER_POSITIVE",
        "INTEGER_NEGATIVE",
        "NUMBER",
        "PERCENTAGE",
        "UNIT_INTERVAL",
    }:
        return f"Value could not be converted to DHIS2 {data_type} format and was discarded."
    return "Value could not be normalized for DHIS2 and was discarded."


def default_import_log_path(input_path: Optional[Path] = None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if input_path is not None:
        return input_path.with_name(f"{input_path.stem}_dhis2_import_log_{timestamp}.csv")
    return Path(__file__).resolve().with_name(f"dhis2_import_log_{timestamp}.csv")


def write_import_value_log(path: Path, issues: Sequence[ImportValueIssue]) -> None:
    fieldnames = [
        "record_id",
        "patient",
        "program",
        "stage",
        "column",
        "field_name",
        "field_id",
        "value",
        "reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for issue in issues:
            writer.writerow(
                {
                    "record_id": issue.record_id,
                    "patient": issue.patient,
                    "program": issue.program,
                    "stage": issue.stage,
                    "column": issue.column,
                    "field_name": issue.field_name,
                    "field_id": issue.field_id,
                    "value": issue.value,
                    "reason": issue.reason,
                }
            )


def format_dhis2_error(exc: Exception) -> str:
    if isinstance(exc, Dhis2RequestError):
        payload = exc.payload
        if isinstance(payload, (dict, list)):
            return json.dumps(payload, ensure_ascii=True)
        return str(payload)
    return str(exc)


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


class Dhis2Client:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = normalize_dhis2_base_url(base_url)
        self.session = requests.Session()
        self.session.verify = False
        self.session.auth = (username, password)
        self.org_unit_cache: Dict[str, str] = {}

    def _request(self, method: str, path: str, **kwargs) -> Dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        response = self.session.request(method, url, timeout=120, **kwargs)
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
            raise Dhis2RequestError(method, url, response.status_code, payload)
        if not response.text.strip():
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    def validate_credentials(self) -> None:
        self._request("GET", "me.json", params={"fields": "id,displayName,username"})

    @staticmethod
    def _extract_import_reference(payload: Dict) -> str:
        response = payload.get("response") if isinstance(payload, dict) else None
        candidates = []
        if isinstance(response, dict):
            candidates.append(str(response.get("reference") or "").strip())
            import_summaries = response.get("importSummaries") or []
            if import_summaries:
                candidates.append(str(import_summaries[0].get("reference") or "").strip())
        import_summaries = payload.get("importSummaries") if isinstance(payload, dict) else None
        if isinstance(import_summaries, list) and import_summaries:
            candidates.append(str(import_summaries[0].get("reference") or "").strip())
        candidates.append(str(payload.get("reference") or "").strip() if isinstance(payload, dict) else "")

        for candidate in candidates:
            if looks_like_uid(candidate):
                return candidate
        return ""

    @staticmethod
    def _extract_conflicting_data_elements(error: Dhis2RequestError) -> List[str]:
        payload = error.payload if isinstance(error.payload, dict) else {}
        conflicts: List[Dict] = []

        def collect_conflicts(value: object) -> None:
            if isinstance(value, dict):
                if any(
                    key in value
                    for key in (
                        "object",
                        "uid",
                        "dataElement",
                        "attribute",
                        "message",
                        "errorMessage",
                        "errorCode",
                    )
                ):
                    conflicts.append(value)
                nested = value.get("conflicts")
                if isinstance(nested, list):
                    conflicts.extend(item for item in nested if isinstance(item, dict))
                for key in (
                    "response",
                    "importSummaries",
                    "importSummary",
                    "validationReport",
                    "validationReports",
                    "trackerTypeReport",
                    "objectReports",
                    "errorReports",
                ):
                    if key in value:
                        collect_conflicts(value[key])
            elif isinstance(value, list):
                for item in value:
                    collect_conflicts(item)

        collect_conflicts(payload)

        data_elements: List[str] = []
        for conflict in conflicts:
            object_id = str(
                conflict.get("object")
                or conflict.get("uid")
                or conflict.get("dataElement")
                or conflict.get("attribute")
                or ""
            ).strip()
            value_code = str(
                conflict.get("value")
                or conflict.get("message")
                or conflict.get("errorMessage")
                or conflict.get("errorCode")
                or ""
            ).strip().casefold()
            value_rejected = (
                value_code.startswith("value_not_valid")
                or "not valid" in value_code
                or "invalid" in value_code
                or "option" in value_code
                or "value_type" in value_code
            )
            if value_rejected:
                candidates = [object_id]
                candidates.extend(re.findall(r"\b[A-Za-z][A-Za-z0-9]{10}\b", value_code))
                args = conflict.get("args")
                if isinstance(args, list):
                    candidates.extend(str(item or "").strip() for item in args)
                for candidate in candidates:
                    if looks_like_uid(candidate):
                        data_elements.append(candidate)
        return data_elements

    def resolve_org_unit(self, org_unit_code: str) -> str:
        code = blank_to_empty(org_unit_code)
        if not code:
            raise RuntimeError("org_unit is blank in the transformed CSV row.")
        if code in self.org_unit_cache:
            return self.org_unit_cache[code]

        if looks_like_uid(code):
            try:
                payload = self._request("GET", f"organisationUnits/{code}.json", params={"fields": "id"})
                org_unit_id = str(payload.get("id") or "").strip()
                if org_unit_id:
                    self.org_unit_cache[code] = org_unit_id
                    return org_unit_id
            except Exception:
                pass

        payload = self._request(
            "GET",
            "organisationUnits.json",
            params={
                "filter": f"code:eq:{code}",
                "fields": "id,code,name",
                "paging": "false",
            },
        )
        organisation_units = payload.get("organisationUnits") or []
        if not organisation_units:
            raise RuntimeError(f"No DHIS2 organisation unit was found for org_unit code '{code}'.")

        org_unit_id = str(organisation_units[0].get("id") or "").strip()
        if not org_unit_id:
            raise RuntimeError(f"Organisation unit lookup for '{code}' returned no UID.")

        self.org_unit_cache[code] = org_unit_id
        return org_unit_id

    def search_tracked_entity(
        self,
        record_attribute_id: str,
        record_id: str,
        tracked_entity_type: str = "",
        program_uid: str = "",
        org_unit_id: str = "",
    ) -> Optional[Dict]:
        params = {
            "ouMode": "ACCESSIBLE",
            "filter": f"{record_attribute_id}:EQ:{record_id}",
            "fields": (
                "trackedEntityInstance,orgUnit,attributes[attribute,value],"
                "enrollments[enrollment,program,status,events[event,programStage,eventDate,status,dataValues[dataElement,value]]]"
            ),
            "paging": "false",
        }
        if tracked_entity_type:
            params["trackedEntityType"] = tracked_entity_type
        if program_uid:
            params["program"] = program_uid
        if org_unit_id:
            params["ou"] = org_unit_id

        payload = self._request(
            "GET",
            "trackedEntityInstances.json",
            params=params,
        )
        instances = payload.get("trackedEntityInstances") or []
        return instances[0] if instances else None

    def _discard_conflicting_attributes(
        self,
        error: Dhis2RequestError,
        attributes: List[Dict[str, str]],
        config: ProgramConfig,
        row: Optional[Dict[str, str]] = None,
        issues: Optional[List[ImportValueIssue]] = None,
    ) -> List[Dict[str, str]]:
        conflicting_ids = set(self._extract_conflicting_data_elements(error))
        conflicting_ids.discard(config.record_id_attribute_id)
        if not conflicting_ids:
            return attributes

        attribute_fields = {field.attribute_id: field for field in config.attributes.values()}
        for item in attributes:
            attribute_id = str(item.get("attribute") or "")
            if attribute_id not in conflicting_ids or row is None:
                continue
            field = attribute_fields.get(attribute_id)
            add_import_value_issue(
                issues,
                row,
                config,
                "Tracked Entity Attributes",
                field.header if field else attribute_id,
                field.attribute_name if field else attribute_id,
                attribute_id,
                str(item.get("value") or ""),
                "DHIS2 rejected this attribute value during import, so the value was discarded and the tracked entity was retried.",
            )

        return [
            item
            for item in attributes
            if str(item.get("attribute") or "") not in conflicting_ids
        ]

    def get_tracked_entity(self, tei_id: str) -> Dict:
        return self._request(
            "GET",
            f"trackedEntityInstances/{tei_id}.json",
            params={
                "fields": (
                    "trackedEntityInstance,orgUnit,attributes[attribute,value],"
                    "enrollments[enrollment,program,status,events[event,programStage,eventDate,status,dataValues[dataElement,value]]]"
                )
            },
        )

    def create_tracked_entity(
        self,
        config: ProgramConfig,
        org_unit_id: str,
        attributes: List[Dict[str, str]],
        row: Optional[Dict[str, str]] = None,
        issues: Optional[List[ImportValueIssue]] = None,
    ) -> str:
        submitted_attributes = list(attributes)
        while True:
            try:
                payload = self._request(
                    "POST",
                    "trackedEntityInstances",
                    json={
                        "trackedEntityType": config.tracked_entity_type,
                        "orgUnit": org_unit_id,
                        "attributes": submitted_attributes,
                    },
                )
                break
            except Dhis2RequestError as exc:
                filtered = self._discard_conflicting_attributes(
                    error=exc,
                    attributes=submitted_attributes,
                    config=config,
                    row=row,
                    issues=issues,
                )
                if len(filtered) == len(submitted_attributes):
                    raise
                submitted_attributes = filtered

        created_id = self._extract_import_reference(payload)
        if created_id:
            return created_id

        record_id = next(
            value["value"]
            for value in attributes
            if value["attribute"] == config.record_id_attribute_id
        )
        created = self.search_tracked_entity(
            record_attribute_id=config.record_id_attribute_id,
            record_id=record_id,
            tracked_entity_type=config.tracked_entity_type,
        )
        if not created:
            raise RuntimeError(f"Tracked entity '{record_id}' was created but could not be looked up afterwards.")
        return str(created.get("trackedEntityInstance") or "").strip()

    def update_tracked_entity(
        self,
        tei_id: str,
        config: ProgramConfig,
        org_unit_id: str,
        attributes: List[Dict[str, str]],
        row: Optional[Dict[str, str]] = None,
        issues: Optional[List[ImportValueIssue]] = None,
    ) -> None:
        submitted_attributes = list(attributes)
        while True:
            try:
                self._request(
                    "PUT",
                    f"trackedEntityInstances/{tei_id}",
                    json={
                        "trackedEntityInstance": tei_id,
                        "trackedEntityType": config.tracked_entity_type,
                        "orgUnit": org_unit_id,
                        "attributes": submitted_attributes,
                    },
                )
                return
            except Dhis2RequestError as exc:
                filtered = self._discard_conflicting_attributes(
                    error=exc,
                    attributes=submitted_attributes,
                    config=config,
                    row=row,
                    issues=issues,
                )
                if len(filtered) == len(submitted_attributes):
                    raise
                submitted_attributes = filtered

    def ensure_enrollment(
        self,
        tei: Dict,
        config: ProgramConfig,
        org_unit_id: str,
        enrollment_date: str,
    ) -> Dict:
        enrollments = tei.get("enrollments") or []
        for enrollment in enrollments:
            if reference_id(enrollment.get("program")) == config.program_uid:
                return enrollment

        self._request(
            "POST",
            "enrollments",
            json={
                "trackedEntityInstance": tei["trackedEntityInstance"],
                "program": config.program_uid,
                "orgUnit": org_unit_id,
                "enrollmentDate": enrollment_date,
                "incidentDate": enrollment_date,
                "status": "ACTIVE",
            },
        )

        refreshed = self.get_tracked_entity(tei["trackedEntityInstance"])
        for enrollment in refreshed.get("enrollments") or []:
            if reference_id(enrollment.get("program")) == config.program_uid:
                return enrollment
        raise RuntimeError(
            f"Enrollment for program {config.program_uid} could not be found after creation."
        )

    def upsert_event(
        self,
        tei_id: str,
        enrollment_id: str,
        org_unit_id: str,
        event_payload: Dict[str, object],
        existing_enrollment: Dict,
        program_uid: str,
        config: Optional[ProgramConfig] = None,
        row: Optional[Dict[str, str]] = None,
        issues: Optional[List[ImportValueIssue]] = None,
    ) -> bool:
        existing_events = existing_enrollment.get("events") or []
        matching_events = [
            event
            for event in existing_events
            if reference_id(event.get("programStage")) == str(event_payload["programStage"])
        ]
        existing_event = matching_events[-1] if matching_events else None

        base_payload = {
            "program": program_uid,
            "programStage": event_payload["programStage"],
            "trackedEntityInstance": tei_id,
            "orgUnit": org_unit_id,
            "enrollment": enrollment_id,
            "eventDate": event_payload["eventDate"],
            "status": "ACTIVE",
        }
        data_values = list(event_payload["dataValues"])
        data_value_fields = event_payload.get("data_value_fields") or {}

        while data_values:
            try:
                if existing_event and existing_event.get("event"):
                    self._request(
                        "PUT",
                        f"events/{existing_event['event']}",
                        json={
                            "event": existing_event["event"],
                            **base_payload,
                            "dataValues": data_values,
                        },
                    )
                    return True

                self._request(
                    "POST",
                    "events",
                    json={**base_payload, "dataValues": data_values},
                )
                return True
            except Dhis2RequestError as exc:
                conflicting_ids = set(self._extract_conflicting_data_elements(exc))
                if not conflicting_ids:
                    if config and row is not None:
                        for item in data_values:
                            data_element_id = str(item.get("dataElement") or "")
                            field_info = (
                                data_value_fields.get(data_element_id)
                                if isinstance(data_value_fields, dict)
                                else {}
                            ) or {}
                            add_import_value_issue(
                                issues,
                                row,
                                config,
                                str(event_payload.get("stage_name") or ""),
                                str(field_info.get("column") or data_element_id),
                                str(field_info.get("field_name") or data_element_id),
                                data_element_id,
                                str(item.get("value") or ""),
                                "DHIS2 rejected the event but did not identify a single bad value; this value was not synced.",
                            )
                    return False
                for item in data_values:
                    data_element_id = str(item.get("dataElement") or "")
                    if data_element_id not in conflicting_ids or not config or row is None:
                        continue
                    field_info = (
                        data_value_fields.get(data_element_id)
                        if isinstance(data_value_fields, dict)
                        else {}
                    ) or {}
                    add_import_value_issue(
                        issues,
                        row,
                        config,
                        str(event_payload.get("stage_name") or ""),
                        str(field_info.get("column") or data_element_id),
                        str(field_info.get("field_name") or data_element_id),
                        data_element_id,
                        str(item.get("value") or ""),
                        "DHIS2 rejected this value during import, so the value was discarded and the event was retried.",
                    )
                filtered = [
                    item for item in data_values if str(item.get("dataElement") or "") not in conflicting_ids
                ]
                if len(filtered) == len(data_values):
                    raise
                data_values = filtered
                continue

        return False


def import_rows(
    base_url: str,
    username: str,
    password: str,
    input_path: Path,
    log_path: Optional[Path] = None,
) -> Dict[str, object]:
    raise_csv_field_limit()
    configs = build_program_configs()
    client = Dhis2Client(base_url=base_url, username=username, password=password)
    client.validate_credentials()
    import_date = today_date()

    counts = {
        "processed": 0,
        "created_entities": 0,
        "updated_entities": 0,
        "created_enrollments": 0,
        "upserted_events": 0,
        "unsynced_values": 0,
        "row_errors": 0,
        "skipped": 0,
    }
    value_issues: List[ImportValueIssue] = []

    with input_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RuntimeError("The selected transformed CSV does not have a header row.")

        required_columns = [column for column in SPECIAL_COLUMNS if column not in reader.fieldnames]
        if required_columns:
            raise RuntimeError(
                "The transformed CSV is missing required column(s): "
                + ", ".join(required_columns)
            )

        for row in reader:
            program_value = normalize_program_value(row.get("program", ""))
            config = configs.get(program_value)
            if not config:
                counts["skipped"] += 1
                continue

            record_id = extract_row_value(row, "Record ID")
            if not record_id:
                counts["skipped"] += 1
                continue

            try:
                org_unit_id = client.resolve_org_unit(extract_row_value(row, "org_unit"))
                attributes = build_attribute_payload(config, row, issues=value_issues)
                enrollment_date = import_date
                stage_payloads = build_stage_payloads(config, row, import_date, issues=value_issues)

                existing = client.search_tracked_entity(
                    record_attribute_id=config.record_id_attribute_id,
                    record_id=record_id,
                    tracked_entity_type=config.tracked_entity_type,
                )

                if existing:
                    tei_id = str(existing.get("trackedEntityInstance") or "").strip()
                    client.update_tracked_entity(
                        tei_id,
                        config,
                        org_unit_id,
                        attributes,
                        row=row,
                        issues=value_issues,
                    )
                    tei = client.get_tracked_entity(tei_id)
                    counts["updated_entities"] += 1
                else:
                    tei_id = client.create_tracked_entity(
                        config,
                        org_unit_id,
                        attributes,
                        row=row,
                        issues=value_issues,
                    )
                    tei = client.get_tracked_entity(tei_id)
                    counts["created_entities"] += 1

                had_enrollment = any(
                    reference_id(enrollment.get("program")) == config.program_uid
                    for enrollment in (tei.get("enrollments") or [])
                )
                enrollment = client.ensure_enrollment(tei, config, org_unit_id, enrollment_date)
                if not had_enrollment:
                    counts["created_enrollments"] += 1

                for event_payload in stage_payloads:
                    event_upserted = client.upsert_event(
                        tei_id=tei_id,
                        enrollment_id=reference_id(enrollment.get("enrollment")),
                        org_unit_id=org_unit_id,
                        event_payload=event_payload,
                        existing_enrollment=enrollment,
                        program_uid=config.program_uid,
                        config=config,
                        row=row,
                        issues=value_issues,
                    )
                    if event_upserted:
                        counts["upserted_events"] += 1

                counts["processed"] += 1
            except Exception as exc:
                counts["row_errors"] += 1
                add_import_value_issue(
                    value_issues,
                    row,
                    config,
                    "Row",
                    "Record ID",
                    "Record ID",
                    config.record_id_attribute_id,
                    record_id,
                    f"Row could not be fully imported: {format_dhis2_error(exc)}",
                )
                continue

    resolved_log_path = log_path or default_import_log_path(input_path)
    write_import_value_log(resolved_log_path, value_issues)
    counts["unsynced_values"] = len(value_issues)
    counts["log_file"] = str(resolved_log_path)
    return counts


class ImportApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("DHIS2 Tracker CSV Import")
        self.root.geometry("920x700")

        self.url_var = tk.StringVar()
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.file_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value="Enter your DHIS2 connection details, choose the transformed CSV, then import."
        )
        self.import_in_progress = False

        self._build_ui()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(container, text="DHIS2 URL").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(container, textvariable=self.url_var, width=60).grid(
            row=row, column=1, sticky="ew", pady=4
        )

        row += 1
        ttk.Label(container, text="Username").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(container, textvariable=self.username_var, width=30).grid(
            row=row, column=1, sticky="w", pady=4
        )

        row += 1
        ttk.Label(container, text="Password").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(container, textvariable=self.password_var, show="*", width=30).grid(
            row=row, column=1, sticky="w", pady=4
        )

        row += 1
        ttk.Label(container, text="Transformed CSV").grid(row=row, column=0, sticky="w", pady=4)
        file_frame = ttk.Frame(container)
        file_frame.grid(row=row, column=1, sticky="ew", pady=4)
        file_frame.columnconfigure(0, weight=1)
        ttk.Entry(file_frame, textvariable=self.file_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(file_frame, text="Browse", command=self.browse_file).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        self.import_button = ttk.Button(
            container,
            text="Import to DHIS2",
            command=self.import_file,
        )
        self.import_button.grid(row=row, column=1, sticky="w", pady=(10, 12))

        row += 1
        ttk.Label(container, textvariable=self.status_var, foreground="#1f4e79").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        row += 1
        log_frame = ttk.LabelFrame(container, text="Import Log", padding=10)
        log_frame.grid(row=row, column=0, columnspan=2, sticky="nsew")
        container.rowconfigure(row, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=20, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_busy(self, busy: bool) -> None:
        self.import_button.configure(state="disabled" if busy else "normal")

    def browse_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose transformed CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.file_var.set(selected)

    def import_file(self) -> None:
        if self.import_in_progress:
            return

        base_url = self.url_var.get().strip()
        username = self.username_var.get().strip()
        password = self.password_var.get()
        input_path = Path(self.file_var.get().strip())

        if not base_url:
            messagebox.showerror("DHIS2 URL required", "Enter the DHIS2 server URL.")
            return
        if not username or not password:
            messagebox.showerror("Credentials required", "Enter the DHIS2 username and password.")
            return
        if not input_path.is_file():
            messagebox.showerror("CSV file required", "Choose a valid transformed CSV file.")
            return

        def worker() -> None:
            self.import_in_progress = True
            self.root.after(0, lambda: self.set_busy(True))
            self.root.after(0, lambda: self.status_var.set("Importing tracker data into DHIS2..."))
            try:
                counts = import_rows(base_url, username, password, input_path)

                def on_success() -> None:
                    self.status_var.set(
                        f"Import complete. {counts['processed']} row(s) processed."
                    )
                    self.log(f"Input file: {input_path}")
                    self.log(f"Rows processed: {counts['processed']}")
                    self.log(f"Tracked entities created: {counts['created_entities']}")
                    self.log(f"Tracked entities updated: {counts['updated_entities']}")
                    self.log(f"Enrollments created: {counts['created_enrollments']}")
                    self.log(f"Events created or updated: {counts['upserted_events']}")
                    self.log(f"Values discarded: {counts['unsynced_values']}")
                    if counts["row_errors"]:
                        self.log(f"Rows with import errors: {counts['row_errors']}")
                    self.log(f"Import value log: {counts['log_file']}")
                    if counts["skipped"]:
                        self.log(f"Rows skipped: {counts['skipped']}")
                    self.set_busy(False)
                    self.import_in_progress = False
                    messagebox.showinfo(
                        "Import complete",
                        f"Processed {counts['processed']} row(s) from:\n{input_path}",
                    )

                self.root.after(0, on_success)
            except Exception as exc:
                self.root.after(0, lambda exc=exc: self._handle_error("Import failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_error(self, title: str, exc: Exception) -> None:
        self.status_var.set(f"{title}: {exc}")
        self.log(f"{title}: {exc}")
        self.set_busy(False)
        self.import_in_progress = False
        messagebox.showerror(title, str(exc))


def main() -> None:
    root = tk.Tk()
    app = ImportApp(root)
    app.log(
        "This importer uses Record ID to find existing tracker records and updates them when they already exist."
    )
    app.log(
        "org_unit values in the CSV are treated as DHIS2 organisation unit codes and resolved to real org unit UIDs."
    )
    root.mainloop()


if __name__ == "__main__":
    main()
