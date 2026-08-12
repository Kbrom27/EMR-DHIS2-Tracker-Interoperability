from __future__ import annotations

import csv
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from clients.openmrs_client import ApiClient
from config import DETAIL_COLUMNS, MATERNAL_PROGRAM, NEONATAL_PROGRAM
from export.extractors import (
    append_unique_value,
    attr_value,
    build_record_id,
    clean_csv_cell,
    collect_orders_and_medications,
    extract_diagnoses,
    extract_entity_name,
    extract_obs_concept,
    extract_obs_value,
    get_root_obs,
    normalize_date_filter,
    safe_dict,
    sanitize_filename,
    validate_date_range,
)
from o3.schemas import FormRegistry, load_default_forms


def determine_program_from_visit_type(visit_type_name: str) -> str:
    normalized = visit_type_name.casefold()
    if "nicu" in normalized:
        return NEONATAL_PROGRAM
    if any(marker in normalized for marker in ("delivery", "labour", "labor")):
        return MATERNAL_PROGRAM
    return ""


def visit_matches_date_range(
    visit: Dict, visit_start_date: Optional[str], visit_end_date: Optional[str]
) -> bool:
    raw_date = visit.get("startDatetime") or visit.get("startDate")
    visit_date = raw_date[:10] if isinstance(raw_date, str) else None
    if visit_date is None:
        return visit_start_date is None and visit_end_date is None
    if visit_start_date and visit_date < visit_start_date:
        return False
    if visit_end_date and visit_date > visit_end_date:
        return False
    return True


def get_patients_by_visit_type(
    visits: Sequence[Dict],
    visit_type_uuid: str,
    visit_start_date: Optional[str],
    visit_end_date: Optional[str],
) -> List[Tuple[str, str, str]]:
    patients: List[Tuple[str, str, str]] = []
    seen = set()

    for visit in visits:
        if safe_dict(visit.get("visitType")).get("uuid") != visit_type_uuid:
            continue
        if not visit_matches_date_range(visit, visit_start_date, visit_end_date):
            continue
        patient = visit.get("patient") or {}
        patient_uuid = patient.get("uuid")
        if patient_uuid and patient_uuid not in seen:
            seen.add(patient_uuid)
            raw_date = visit.get("startDatetime") or visit.get("startDate") or ""
            visit_date = raw_date[:10] if isinstance(raw_date, str) else ""
            patients.append((patient_uuid, patient.get("display", ""), visit_date))

    return patients


def o3_concept_label(obs: Dict, registry: FormRegistry) -> str:
    concept = obs.get("concept")
    if isinstance(concept, dict):
        uuid = concept.get("uuid", "")
        label = registry.concept_label(uuid)
        if label:
            return label
    return extract_obs_concept(obs)


def o3_obs_source_label(
    encounter: Optional[Dict],
    registry: FormRegistry,
    template_label: str = "",
) -> str:
    if isinstance(encounter, dict):
        form_name = extract_entity_name(encounter.get("form"))
        if form_name:
            return form_name
        form = encounter.get("form")
        if isinstance(form, dict):
            registered = registry.form_name_for_uuid(str(form.get("uuid") or "").strip())
            if registered:
                return registered
        encounter_type = encounter.get("encounterType") or {}
        registered = registry.form_name_for_encounter_type(
            str(encounter_type.get("uuid") or "").strip()
        )
        if registered:
            return registered
        encounter_type_label = extract_entity_name(encounter.get("encounterType"))
        if encounter_type_label:
            return encounter_type_label
    if template_label:
        return template_label
    return ""


def build_o3_obs_column(concept: str, source_label: str) -> str:
    if source_label:
        return f"{concept} [{source_label}]"
    return concept


def flatten_o3_obs(
    obs_list: Sequence[Dict],
    registry: FormRegistry,
    default_encounter: Optional[Dict] = None,
) -> List[Tuple[str, str, str]]:
    flattened: List[Tuple[str, str, str]] = []

    def visit_obs(
        obs: Dict,
        inherited_encounter: Optional[Dict],
        template_label: str = "",
    ) -> None:
        if not isinstance(obs, dict):
            return
        encounter = obs.get("encounter")
        if not isinstance(encounter, dict) or not encounter:
            encounter = inherited_encounter

        current_template_label = template_label
        group_members = obs.get("groupMembers")
        has_group_members = isinstance(group_members, list) and bool(group_members)
        if not current_template_label and has_group_members:
            current_template_label = o3_concept_label(obs, registry)

        concept = o3_concept_label(obs, registry)
        value = extract_obs_value(obs)
        if concept and value and not has_group_members:
            column = build_o3_obs_column(
                concept,
                o3_obs_source_label(encounter, registry, current_template_label),
            )
            flattened.append((concept, column, value))
        if has_group_members:
            for member in group_members:
                visit_obs(member, encounter, current_template_label)

    for obs in get_root_obs(obs_list):
        visit_obs(obs, default_encounter)

    return flattened


def flatten_o3_encounter_obs(
    encounters: Sequence[Dict],
    registry: FormRegistry,
) -> List[Tuple[str, str, str]]:
    flattened: List[Tuple[str, str, str]] = []
    for encounter in encounters:
        encounter_obs = encounter.get("obs")
        if not isinstance(encounter_obs, list):
            continue
        flattened.extend(flatten_o3_obs(encounter_obs, registry, default_encounter=encounter))
    return flattened


def build_o3_patient_row(
    api: ApiClient,
    registry: FormRegistry,
    patient_uuid: str,
    display: str,
    org_unit_code: str,
    program_value: str,
    visit_date: str = "",
) -> Tuple[List[str], Dict[str, str]]:
    patient_id = display.split(" - ", 1)[0].strip()
    record_id = build_record_id(org_unit_code, patient_id)

    person = api.get_patient_person(patient_uuid) or {}
    obs_list = api.get_patient_obs(patient_uuid) or []
    encounters = api.get_patient_encounters(patient_uuid) or []
    direct_orders = api.get_patient_orders(patient_uuid) or []

    encounter_obs_entries = flatten_o3_encounter_obs(encounters, registry)
    seen_encounter_pairs = {(concept, value) for concept, _column, value in encounter_obs_entries}
    standalone_obs_entries: List[Tuple[str, str, str]] = []
    for concept, column, value in flatten_o3_obs(obs_list, registry):
        if (concept, value) in seen_encounter_pairs:
            continue
        standalone_obs_entries.append((concept, column, value))

    combined_obs_entries = encounter_obs_entries + standalone_obs_entries
    diagnoses = extract_diagnoses(combined_obs_entries)
    orders, medications, lab_results = collect_orders_and_medications(direct_orders, encounters)

    preferred_name = person.get("preferredName") or {}
    preferred_address = person.get("preferredAddress") or {}
    cause_of_death = person.get("causeOfDeath", {})
    if isinstance(cause_of_death, dict):
        cause_of_death = cause_of_death.get("display", "")

    row = [
        patient_uuid,
        display,
        patient_id,
        org_unit_code,
        record_id,
        program_value,
        str(preferred_name.get("givenName", "")),
        str(preferred_name.get("familyName", "")),
        str(person.get("age", "")),
        str(person.get("gender", "")),
        str(person.get("birthdate", "")),
        str(person.get("deathDate", "")),
        str(cause_of_death or ""),
        str(preferred_address.get("address1", "")),
        str(preferred_address.get("address2", "")),
        str(preferred_address.get("address3", "")),
        str(preferred_address.get("cityVillage", "")),
        str(preferred_address.get("stateProvince", "")),
        str(preferred_address.get("countyDistrict", "")),
        str(safe_dict(person.get("auditInfo")).get("dateCreated", "")),
        attr_value(person, "givenNameLocal"),
        attr_value(person, "familyNameLocal"),
        attr_value(person, "middleNameLocal"),
        attr_value(person, "caste"),
        attr_value(person, "class"),
        attr_value(person, "Education Details"),
        attr_value(person, "occupation"),
        attr_value(person, "primaryContact"),
        attr_value(person, "secondaryContact"),
        attr_value(person, "Father/Husband Name"),
        attr_value(person, "Secondary Identifier"),
        attr_value(person, "landHolding"),
        attr_value(person, "debt"),
        attr_value(person, "distanceFromCenter"),
        attr_value(person, "isUrban"),
        attr_value(person, "cluster"),
        attr_value(person, "Ration Card Type"),
        attr_value(person, "familyIncome"),
        attr_value(person, "email"),
        attr_value(person, "Payment Method"),
        attr_value(person, "CBHI ID"),
        attr_value(person, "Expiry Date"),
        visit_date,
        " | ".join(diagnoses),
        " | ".join(lab_results),
        " | ".join(orders),
        " | ".join(medications),
    ]

    obs_values: Dict[str, str] = OrderedDict()
    for _concept, column, value in combined_obs_entries:
        append_unique_value(obs_values, column, value)

    return row, obs_values


def write_o3_patients_csv(
    api: ApiClient,
    registry: FormRegistry,
    patients: Sequence[Tuple[str, str, str]],
    output_filename: Path,
    org_unit_code: str,
    program_value: str,
    fetch_concurrency: int = 4,
) -> int:
    all_obs_columns: List[str] = []
    seen_obs_columns = set()
    buffered_rows: List[Tuple[List[str], Dict[str, str]]] = []

    with ThreadPoolExecutor(max_workers=max(1, fetch_concurrency)) as executor:
        futures = {
            executor.submit(
                build_o3_patient_row,
                api,
                registry,
                patient_uuid,
                display,
                org_unit_code,
                program_value,
                visit_date,
            ): (patient_uuid, display)
            for patient_uuid, display, visit_date in patients
        }

        for future in as_completed(futures):
            fixed_row, obs_values = future.result()
            for column in obs_values:
                if column not in seen_obs_columns:
                    seen_obs_columns.add(column)
                    all_obs_columns.append(column)
            buffered_rows.append((fixed_row, obs_values))

    output_filename.parent.mkdir(parents=True, exist_ok=True)
    with output_filename.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\n")
        header = [clean_csv_cell(column) for column in DETAIL_COLUMNS + all_obs_columns]
        writer.writerow(header)
        for fixed_row, obs_values in buffered_rows:
            row = [clean_csv_cell(value) for value in fixed_row]
            for column in all_obs_columns:
                row.append(clean_csv_cell(obs_values.get(column, "")))
            writer.writerow(row)

    return len(buffered_rows)
