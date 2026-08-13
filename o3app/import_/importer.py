from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from o3app.clients.dhis2_client import (
    Dhis2Client,
    add_import_value_issue,
    format_dhis2_error,
    reference_id,
    today_date,
)
from o3app.config import SPECIAL_COLUMNS, normalize_program_value
from o3app.import_.payload_builder import (
    build_attribute_payload,
    build_program_configs,
    build_stage_payloads,
    extract_row_value,
    infer_enrollment_date,
)
from o3app.models import ImportValueIssue
from o3app.utils import raise_csv_field_limit


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
                enrollment_date = infer_enrollment_date(config, row)
                stage_payloads = build_stage_payloads(
                    config,
                    row,
                    enrollment_date or import_date,
                    issues=value_issues,
                )

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
