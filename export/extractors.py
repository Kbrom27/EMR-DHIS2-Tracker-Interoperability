from __future__ import annotations

import csv
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from clients.openmrs_client import ApiClient
from config import DETAIL_COLUMNS, MATERNAL_PROGRAM, NEONATAL_PROGRAM, is_patient_eligible_for_program
from utils import clean_csv_cell


def safe_dict(value: object) -> Dict:
    return value if isinstance(value, dict) else {}


def normalize_date_filter(raw: str) -> Optional[str]:
    trimmed = raw.strip()
    if not trimmed:
        return None
    try:
        return datetime.strptime(trimmed[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Dates must use YYYY-MM-DD format.") from exc


def validate_date_range(start_date: Optional[str], end_date: Optional[str]) -> None:
    if start_date and end_date and start_date > end_date:
        raise ValueError("End date must be the same as or later than the start date.")


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._") or "openmrs_export"


def determine_program_from_visit_type(visit_type_name: str) -> str:
    normalized = visit_type_name.casefold()
    if any(marker in normalized for marker in ("nicu", "neonatal", "ncu")):
        return NEONATAL_PROGRAM
    if any(marker in normalized for marker in ("delivery", "labour", "labor", "obs", "obstetric")):
        return MATERNAL_PROGRAM
    return ""


def build_record_id(org_unit_code: str, patient_id: str) -> str:
    normalized_patient_id = re.sub(r"\D", "", patient_id)
    if not normalized_patient_id:
        normalized_patient_id = patient_id.strip()
    return f"{org_unit_code}{normalized_patient_id.zfill(10)}"


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


def is_on_or_after_visit(date_val: Optional[str], visit_date: str) -> bool:
    if not visit_date or not date_val:
        return True
    return str(date_val)[:10] >= str(visit_date)[:10]


def split_obs_display(obs: Dict) -> Tuple[str, str]:
    display = str(obs.get("display") or "").strip()
    if not display:
        return "", ""

    for separator in (": ", " = "):
        if separator in display:
            concept, value = display.split(separator, 1)
            return concept.strip(), value.strip()

    return "", ""


def extract_obs_display_value(obs: Dict, concept: str) -> str:
    display = str(obs.get("display") or "").strip()
    if not display:
        return ""

    if concept and display.casefold().startswith(concept.casefold()):
        remainder = display[len(concept):].strip()
        if remainder.startswith(":") or remainder.startswith("="):
            return remainder[1:].strip()
        if not remainder:
            return ""
        return ""

    _display_concept, display_value = split_obs_display(obs)
    return display_value


def extract_obs_concept(obs: Dict) -> str:
    concept = extract_entity_name(obs.get("concept"))
    if concept:
        return concept
    display_concept, _display_value = split_obs_display(obs)
    return display_concept


def extract_obs_value(obs: Dict) -> str:
    concept = extract_entity_name(obs.get("concept"))
    display_value = extract_obs_display_value(obs, concept)
    if display_value:
        return display_value

    value = obs.get("value")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        display = value.get("display")
        if isinstance(display, str):
            return display.strip()
        uuid = value.get("uuid")
        if uuid:
            return str(uuid)
        return str(value).strip()
    return ""


def extract_entity_name(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        display = value.get("display")
        if isinstance(display, str) and display.strip():
            return display.strip()
        uuid = value.get("uuid")
        if uuid:
            return str(uuid).strip()
    if value is None:
        return ""
    return str(value).strip()


def extract_obs_source_label(
    encounter: Optional[Dict],
    template_label: str = "",
) -> str:
    if template_label:
        return template_label

    if isinstance(encounter, dict):
        form_name = extract_entity_name(encounter.get("form"))
        encounter_type = extract_entity_name(encounter.get("encounterType"))
        if form_name and form_name.lower() != encounter_type.lower():
            return form_name
        if encounter_type:
            return encounter_type

    return ""


def build_obs_column_name(concept: str, source_label: str) -> str:
    if source_label:
        return f"{concept} [{source_label}]"
    return concept


def append_unique_value(values: Dict[str, str], key: str, value: str) -> None:
    existing = values.get(key, "")
    if not existing:
        values[key] = value
        return

    existing_parts = {part.strip() for part in existing.split(" | ") if part.strip()}
    if value not in existing_parts:
        values[key] = f"{existing} | {value}"


def collect_group_member_uuids(obs: object) -> set[str]:
    uuids: set[str] = set()
    if not isinstance(obs, dict):
        return uuids
    group_members = obs.get("groupMembers")
    if not isinstance(group_members, list):
        return uuids

    for member in group_members:
        if not isinstance(member, dict):
            continue
        member_uuid = str(member.get("uuid") or "").strip()
        if member_uuid:
            uuids.add(member_uuid)
        uuids.update(collect_group_member_uuids(member))

    return uuids


def get_root_obs(obs_list: Sequence[object]) -> List[Dict]:
    child_uuids: set[str] = set()
    for obs in obs_list:
        child_uuids.update(collect_group_member_uuids(obs))

    dict_obs = [obs for obs in obs_list if isinstance(obs, dict)]
    if not child_uuids:
        return dict_obs

    roots: List[Dict] = []
    for obs in dict_obs:
        obs_uuid = str(obs.get("uuid") or "").strip()
        if obs_uuid and obs_uuid in child_uuids:
            continue
        roots.append(obs)

    return roots or dict_obs


def flatten_obs(
    obs_list: Sequence[Dict],
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
            current_template_label = extract_obs_concept(obs)

        concept = extract_obs_concept(obs)
        value = extract_obs_value(obs)
        if concept and value and not has_group_members:
            column = build_obs_column_name(
                concept,
                extract_obs_source_label(encounter, current_template_label),
            )
            flattened.append((concept, column, value))
        if has_group_members:
            for member in group_members:
                visit_obs(member, encounter, current_template_label)

    for obs in get_root_obs(obs_list):
        visit_obs(obs, default_encounter)

    return flattened


def flatten_encounter_obs(encounters: Sequence[Dict]) -> List[Tuple[str, str, str]]:
    flattened: List[Tuple[str, str, str]] = []
    for encounter in encounters:
        encounter_obs = encounter.get("obs")
        if not isinstance(encounter_obs, list):
            continue
        flattened.extend(flatten_obs(encounter_obs, default_encounter=encounter))
    return flattened


def is_diagnosis_concept(concept_name: str) -> bool:
    concept_upper = concept_name.upper()
    markers = (
        "DIAGNOSIS",
        "VISIT DIAGNOSES",
        "CODED DIAGNOSIS",
        "NON-CODED DIAGNOSIS",
        "PRIMARY DIAGNOSIS",
        "SECONDARY DIAGNOSIS",
        "PROVISIONAL DIAGNOSIS",
        "FINAL DIAGNOSIS",
    )
    return any(marker in concept_upper for marker in markers)


def extract_diagnoses(obs_entries: Sequence[Tuple[str, str, str]]) -> List[str]:
    diagnoses: List[str] = []
    seen = set()
    for concept, _column, value in obs_entries:
        if not is_diagnosis_concept(concept):
            continue
        if value not in seen:
            seen.add(value)
            diagnoses.append(value)
    return diagnoses


def attr_value(person: Dict, key: str) -> str:
    attributes = person.get("attributes")
    if not isinstance(attributes, list):
        return ""

    for attribute in attributes:
        attribute_type = str(safe_dict(attribute.get("attributeType")).get("display", ""))
        if attribute_type.lower() != key.lower():
            continue
        value = attribute.get("value")
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            display = value.get("display")
            if isinstance(display, str):
                return display
        if value is not None:
            return str(value)

    return ""


def describe_order(order: Dict) -> Tuple[Optional[str], Optional[str]]:
    order_type = str(
        safe_dict(order.get("orderType")).get("display")
        or order.get("type")
        or order.get("typeOfOrder")
        or ""
    ).strip()
    concept_name = str(safe_dict(order.get("concept")).get("display") or "").strip()
    drug_name = str(safe_dict(order.get("drug")).get("display") or "").strip()
    instructions = str(order.get("instructions") or "").strip()
    comment = str(order.get("commentToFulfiller") or "").strip()
    action = str(order.get("action") or "").strip()
    frequency = str(safe_dict(order.get("frequency")).get("display") or "").strip()
    route = str(safe_dict(order.get("route")).get("display") or "").strip()
    dose = str(order.get("dose") or "").strip()
    dose_units = str(safe_dict(order.get("doseUnits")).get("display") or "").strip()
    duration = str(order.get("duration") or "").strip()
    duration_units = str(safe_dict(order.get("durationUnits")).get("display") or "").strip()
    quantity = str(order.get("quantity") or "").strip()
    display = str(order.get("display") or "").strip()

    medication_name = drug_name or concept_name
    med_parts = [medication_name]
    if dose:
        med_parts.append(dose)
    if dose_units:
        med_parts.append(dose_units)
    if frequency:
        med_parts.append(frequency)
    if duration:
        med_parts.append(duration)
    if duration_units:
        med_parts.append(duration_units)
    if quantity:
        med_parts.append(f"qty:{quantity}")
    if route:
        med_parts.append(f"route:{route}")
    if instructions:
        med_parts.append(f"instructions:{instructions}")

    order_parts = [concept_name or drug_name or display]
    if order_type:
        order_parts.append(f"type:{order_type}")
    if action:
        order_parts.append(f"action:{action}")
    if instructions:
        order_parts.append(f"instructions:{instructions}")
    if comment:
        order_parts.append(f"comment:{comment}")

    medication_text = " ".join(part for part in med_parts if part).strip()
    order_text = " ".join(part for part in order_parts if part).strip()

    is_drug_order = bool(drug_name) or "drug" in order_type.lower()
    order_type_lower = order_type.lower()
    is_lab_order = any(
        marker in order_type_lower
        for marker in ("lab", "test", "radiology", "imaging", "pathology", "specimen")
    )

    if is_drug_order:
        return (None, medication_text or display or None, None)
    if is_lab_order:
        lab_parts = [concept_name or display]
        if instructions:
            lab_parts.append(f"instructions:{instructions}")
        if comment:
            lab_parts.append(f"comment:{comment}")
        lab_text = " ".join(part for part in lab_parts if part).strip()
        return (None, None, lab_text or display or None)
    return (order_text or display or None, None, None)


def collect_orders_and_medications(
    direct_orders: Sequence[Dict], encounters: Sequence[Dict]
) -> Tuple[List[str], List[str], List[str]]:
    orders: List[str] = []
    medications: List[str] = []
    lab_results: List[str] = []
    seen_orders = set()
    seen_meds = set()
    seen_labs = set()

    def add(order_text: Optional[str], med_text: Optional[str], lab_text: Optional[str] = None) -> None:
        if order_text and order_text not in seen_orders:
            seen_orders.add(order_text)
            orders.append(order_text)
        if med_text and med_text not in seen_meds:
            seen_meds.add(med_text)
            medications.append(med_text)
        if lab_text and lab_text not in seen_labs:
            seen_labs.add(lab_text)
            lab_results.append(lab_text)

    for order in direct_orders:
        if isinstance(order, dict):
            add(*describe_order(order))

    for encounter in encounters:
        if not isinstance(encounter, dict):
            continue
        for order in encounter.get("orders") or []:
            if isinstance(order, dict):
                add(*describe_order(order))

    return orders, medications, lab_results


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


def build_patient_row(
    api: ApiClient,
    patient_uuid: str,
    display: str,
    org_unit_code: str,
    program_value: str,
    visit_date: str = "",
) -> Tuple[List[str], Dict[str, str]]:
    patient_id = display.split(" - ", 1)[0].strip()
    record_id = build_record_id(org_unit_code, patient_id)

    person = api.get_patient_person(patient_uuid)
    if not is_patient_eligible_for_program(person.get("gender"), person.get("age"), program_value):
        return [], {}

    obs_list = api.get_patient_obs(patient_uuid)
    encounters = api.get_patient_encounters(patient_uuid)
    direct_orders = api.get_patient_orders(patient_uuid)

    if visit_date:
        encounters = [
            enc for enc in encounters
            if is_on_or_after_visit(enc.get("encounterDatetime") or enc.get("encounterDate"), visit_date)
        ]
        filtered_obs = []
        for obs in obs_list:
            obs_dt = obs.get("obsDatetime")
            if not obs_dt and isinstance(obs.get("encounter"), dict):
                obs_dt = obs.get("encounter", {}).get("encounterDatetime")
            if is_on_or_after_visit(obs_dt, visit_date):
                filtered_obs.append(obs)
        obs_list = filtered_obs
        direct_orders = [
            ord_item for ord_item in direct_orders
            if is_on_or_after_visit(ord_item.get("dateActivated") or ord_item.get("dateCreated") or ord_item.get("scheduledDate"), visit_date)
        ]

    encounter_obs_entries = flatten_encounter_obs(encounters)
    seen_encounter_pairs = {(concept, value) for concept, _column, value in encounter_obs_entries}
    standalone_obs_entries: List[Tuple[str, str, str]] = []
    for concept, column, value in flatten_obs(obs_list):
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


def write_patients_csv(
    api: ApiClient,
    patients: Sequence[Tuple[str, str, str]],
    output_filename: Path,
    org_unit_code: str,
    program_value: str,
    fetch_concurrency: int = 12,
) -> int:
    all_obs_columns: List[str] = []
    seen_obs_columns = set()
    buffered_rows: List[Tuple[List[str], Dict[str, str]]] = []

    with ThreadPoolExecutor(max_workers=max(1, fetch_concurrency)) as executor:
        futures = {
            executor.submit(
                build_patient_row,
                api,
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
            if not fixed_row:
                continue
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
