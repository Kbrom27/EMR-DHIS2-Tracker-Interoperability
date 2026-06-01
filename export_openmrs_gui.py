import csv
import re
import threading
import tkinter as tk
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

import requests
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


MATERNAL_PROGRAM = "Maternal Inpatient Data/aLoraiFNkng"
NEONATAL_PROGRAM = "Neonatal Care Form/QYJKpoUeg9F"


DETAIL_COLUMNS = [
    "patient_uuid",
    "patient_display",
    "patient_id",
    "org_unit",
    "Record ID",
    "program",
    "first_name",
    "family_name",
    "age",
    "gender",
    "birth_date",
    "death_date",
    "cause_of_death",
    "address1",
    "address2",
    "address3",
    "city_village",
    "state_province",
    "county_district",
    "registration_date",
    "name_in_local_language",
    "family_name_local",
    "middle_name_local",
    "caste",
    "class",
    "education_details",
    "occupation",
    "primary_contact",
    "secondary_contact",
    "fathers_husbands_name",
    "secondary_identifier",
    "land_holding_acres",
    "debt_rs",
    "distance_from_center_km",
    "urban",
    "cluster",
    "ration_card_type",
    "family_income_per_month_rs",
    "email_address",
    "payment_method",
    "cbhi_id",
    "expiry_date",
    "visit_date",
    "diagnoses",
    "lab_results",
    "orders",
    "medications",
]


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
    if "nicu" in normalized:
        return NEONATAL_PROGRAM
    if any(marker in normalized for marker in ("delivery", "labour", "labor")):
        return MATERNAL_PROGRAM
    return ""


def build_record_id(org_unit_code: str, patient_id: str) -> str:
    normalized_patient_id = re.sub(r"\D", "", patient_id)
    if not normalized_patient_id:
        normalized_patient_id = patient_id.strip()
    return f"{org_unit_code}{normalized_patient_id.zfill(10)}"


def normalize_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        raise ValueError("OpenMRS server/IP is required.")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    lower = value.lower()
    if lower.endswith("/ws/rest/v1"):
        return value
    if "/ws/rest/" in lower:
        return value.rstrip("/")
    if lower.endswith("/openmrs"):
        return f"{value}/ws/rest/v1"
    return f"{value}/openmrs/ws/rest/v1"


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
            # display is exactly the concept name with no value
            return ""
        # The concept name is only a prefix of a longer concept name (e.g. "Blood Pressure"
        # matching "Blood Pressure Systolic: 120"). Do NOT fall through to split_obs_display
        # here — that would extract the value of the wrong concept. Return empty and let
        # extract_obs_value fall back to the structured obs.get("value") field instead.
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


def clean_csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[\r\n\t]+", " ", text).strip()
    return text.replace(",", ";")


def collect_group_member_uuids(obs: Dict) -> set[str]:
    uuids: set[str] = set()
    group_members = obs.get("groupMembers")
    if not isinstance(group_members, list):
        return uuids

    for member in group_members:
        member_uuid = str(member.get("uuid") or "").strip()
        if member_uuid:
            uuids.add(member_uuid)
        uuids.update(collect_group_member_uuids(member))

    return uuids


def get_root_obs(obs_list: Sequence[Dict]) -> List[Dict]:
    child_uuids: set[str] = set()
    for obs in obs_list:
        child_uuids.update(collect_group_member_uuids(obs))

    if not child_uuids:
        return list(obs_list)

    roots: List[Dict] = []
    for obs in obs_list:
        obs_uuid = str(obs.get("uuid") or "").strip()
        if obs_uuid and obs_uuid in child_uuids:
            continue
        roots.append(obs)

    return roots or list(obs_list)


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
        attribute_type = str(attribute.get("attributeType", {}).get("display", ""))
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


class ApiClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.verify = False
        self.session_ok = False

    def _sync_base_url_from_response(self, response: requests.Response) -> None:
        final_url = response.url
        if not final_url:
            return
        parts = urlsplit(final_url)
        path = parts.path.rstrip("/")
        marker = "/ws/rest/v1"
        lower_path = path.lower()
        marker_index = lower_path.find(marker)
        if marker_index == -1:
            return
        rest_path = path[: marker_index + len(marker)]
        self.base_url = f"{parts.scheme}://{parts.netloc}{rest_path}"

    def login_session(self) -> bool:
        url = f"{self.base_url}/session"
        response = self.session.get(url, auth=(self.username, self.password), timeout=60)
        response.raise_for_status()
        self._sync_base_url_from_response(response)
        payload = response.json()
        return bool(payload.get("authenticated"))

    def get_json(self, url: str, params: Optional[Dict[str, str]] = None) -> Dict:
        kwargs = {"params": params, "timeout": 120}
        if not self.session_ok:
            kwargs["auth"] = (self.username, self.password)
        response = self.session.get(url, **kwargs)
        response.raise_for_status()
        self._sync_base_url_from_response(response)
        return response.json()

    def get_all_results_by_params(
        self, resource: str, params: Dict[str, str], limit: int = 100
    ) -> List[Dict]:
        start_index = 0
        all_results: List[Dict] = []

        while True:
            page_params = dict(params)
            page_params["limit"] = str(limit)
            page_params["startIndex"] = str(start_index)
            url = f"{self.base_url}/{resource.lstrip('/')}"
            raw = self.get_json(url, params=page_params)
            results = raw.get("results") or []
            if not results:
                break
            all_results.extend(results)
            if len(results) < limit:
                break
            start_index += limit

        return all_results

    def get_all_results(self, path: str, limit: int = 100) -> List[Dict]:
        start_index = 0
        all_results: List[Dict] = []
        separator = "&" if "?" in path else "?"

        while True:
            url = f"{self.base_url}/{path}{separator}limit={limit}&startIndex={start_index}"
            raw = self.get_json(url)
            results = raw.get("results") or []
            if not results:
                break
            all_results.extend(results)
            if len(results) < limit:
                break
            start_index += limit

        return all_results

    def get_visit_types(self) -> List[Dict]:
        return self.get_all_results("visittype?v=default")

    def get_visits(
        self,
        visit_start_date: Optional[str],
        visit_end_date: Optional[str],
        page_size: int,
    ) -> List[Dict]:
        params = {
            "includeInactive": "true",
            "v": "custom:(uuid,startDatetime,patient:(uuid,display),visitType:(uuid,name))",
        }
        if visit_start_date:
            params["fromStartDate"] = f"{visit_start_date}T00:00:00.000Z"
        if visit_end_date:
            params["toStartDate"] = f"{visit_end_date}T23:59:59.999Z"
        return self.get_all_results_by_params("visit", params=params, limit=page_size)

    def get_patient_person(self, patient_uuid: str) -> Dict:
        person_url = urljoin(self.base_url + "/", f"person/{patient_uuid}")
        return self.get_json(
            person_url,
            params={
                "v": (
                    "custom:(age,gender,birthdate,deathDate,causeOfDeath,"
                    "preferredName:(givenName,familyName),"
                    "preferredAddress:(address1,address2,address3,cityVillage,stateProvince,countyDistrict),"
                    "auditInfo:(dateCreated),attributes:(attributeType:(display),value))"
                )
            },
        )

    def get_patient_obs(self, patient_uuid: str) -> List[Dict]:
        preferred_paths = [
            (
                f"obs?patient={patient_uuid}"
                "&v=custom:(uuid,display,concept:(name,display),value,"
                "encounter:(encounterType:(display),form:(name,display,uuid)),"
                "groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value)))))"
            ),
            (
                f"obs?patient={patient_uuid}"
                "&v=custom:(uuid,display,concept:(name,display),value,"
                "encounter:(encounterType:(display)),"
                "groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value)))))"
            ),
            (
                f"obs?patient={patient_uuid}"
                "&v=custom:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value))))"
            ),
        ]
        for path in preferred_paths:
            try:
                return self.get_all_results(path)
            except requests.HTTPError:
                continue
        return []

    def get_patient_orders(self, patient_uuid: str) -> List[Dict]:
        preferred_paths = [
            (
                f"order?patient={patient_uuid}"
                "&v=custom:(display,action,instructions,commentToFulfiller,"
                "orderType:(display),type,concept:(display),drug:(display),dose,"
                "doseUnits:(display),frequency:(display),duration,durationUnits:(display),"
                "quantity,route:(display))"
            ),
            (
                f"order?patient={patient_uuid}"
                "&v=custom:(display,orderType:(display),concept:(display),drug:(display))"
            ),
            f"order?patient={patient_uuid}&v=default",
        ]
        for path in preferred_paths:
            try:
                return self.get_all_results(path)
            except requests.HTTPError:
                continue
        return []

    def get_patient_encounters(self, patient_uuid: str) -> List[Dict]:
        preferred_paths = [
            (
                f"encounter?patient={patient_uuid}"
                "&v=custom:(encounterDatetime,encounterType:(display),form:(name,display,uuid),"
                "obs:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value)))),"
                "orders:(display,action,instructions,commentToFulfiller,orderType:(display),type,"
                "concept:(display),drug:(display),dose,doseUnits:(display),frequency:(display),"
                "duration,durationUnits:(display),quantity,route:(display)))"
            ),
            (
                f"encounter?patient={patient_uuid}"
                "&v=custom:(encounterDatetime,encounterType:(display),"
                "obs:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value,groupMembers:(uuid,display,concept:(name,display),value))),"
                "orders:(display,orderType:(display),concept:(display),drug:(display)))"
            ),
        ]
        for path in preferred_paths:
            try:
                return self.get_all_results(path)
            except requests.HTTPError:
                continue
        return []


def describe_order(order: Dict) -> Tuple[Optional[str], Optional[str]]:
    order_type = str(
        order.get("orderType", {}).get("display")
        or order.get("type")
        or order.get("typeOfOrder")
        or ""
    ).strip()
    concept_name = str(order.get("concept", {}).get("display") or "").strip()
    drug_name = str(order.get("drug", {}).get("display") or "").strip()
    instructions = str(order.get("instructions") or "").strip()
    comment = str(order.get("commentToFulfiller") or "").strip()
    action = str(order.get("action") or "").strip()
    frequency = str(order.get("frequency", {}).get("display") or "").strip()
    route = str(order.get("route", {}).get("display") or "").strip()
    dose = str(order.get("dose") or "").strip()
    dose_units = str(order.get("doseUnits", {}).get("display") or "").strip()
    duration = str(order.get("duration") or "").strip()
    duration_units = str(order.get("durationUnits", {}).get("display") or "").strip()
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
        add(*describe_order(order))

    for encounter in encounters:
        for order in encounter.get("orders") or []:
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
        if visit.get("visitType", {}).get("uuid") != visit_type_uuid:
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
    obs_list = api.get_patient_obs(patient_uuid)
    encounters = api.get_patient_encounters(patient_uuid)
    direct_orders = api.get_patient_orders(patient_uuid)

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
        str(person.get("auditInfo", {}).get("dateCreated", "")),
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
    fetch_concurrency: int = 4,
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


class ExportApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("OpenMRS Patient Export")
        self.root.geometry("860x620")

        self.api: Optional[ApiClient] = None
        self.visit_types: List[Dict] = []
        self.export_in_progress = False

        self.base_url_var = tk.StringVar()
        self.username_var = tk.StringVar(value="superman")
        self.password_var = tk.StringVar(value="Admin123")
        self.start_date_var = tk.StringVar()
        self.end_date_var = tk.StringVar()
        self.visit_type_var = tk.StringVar()
        self.output_var = tk.StringVar(
            value=str(Path(__file__).resolve().with_name("openmrs_export.csv"))
        )
        self.status_var = tk.StringVar(
            value="Enter OpenMRS connection details, then load visit types."
        )

        self._build_ui()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(container, text="EMR Server / IP").grid(
            row=row, column=0, sticky="w", pady=4
        )
        ttk.Entry(container, textvariable=self.base_url_var, width=60).grid(
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
        button_row = ttk.Frame(container)
        button_row.grid(row=row, column=1, sticky="w", pady=(6, 10))
        self.connect_button = ttk.Button(
            button_row,
            text="Connect and Load Visit Types",
            command=self.load_visit_types,
        )
        self.connect_button.pack(side="left")

        row += 1
        ttk.Label(container, text="Start Date").grid(row=row, column=0, sticky="w", pady=4)
        date_frame = ttk.Frame(container)
        date_frame.grid(row=row, column=1, sticky="w", pady=4)
        ttk.Entry(date_frame, textvariable=self.start_date_var, width=14).pack(side="left")
        ttk.Label(date_frame, text="YYYY-MM-DD").pack(side="left", padx=(8, 18))
        ttk.Label(date_frame, text="End Date").pack(side="left")
        ttk.Entry(date_frame, textvariable=self.end_date_var, width=14).pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(date_frame, text="YYYY-MM-DD").pack(side="left", padx=(8, 0))

        row += 1
        ttk.Label(container, text="Visit Type").grid(row=row, column=0, sticky="w", pady=4)
        self.visit_type_combo = ttk.Combobox(
            container,
            textvariable=self.visit_type_var,
            state="readonly",
            width=57,
        )
        self.visit_type_combo.grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        ttk.Label(container, text="Output CSV").grid(row=row, column=0, sticky="w", pady=4)
        output_frame = ttk.Frame(container)
        output_frame.grid(row=row, column=1, sticky="ew", pady=4)
        output_frame.columnconfigure(0, weight=1)
        ttk.Entry(output_frame, textvariable=self.output_var).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(output_frame, text="Browse", command=self.browse_output).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        self.export_button = ttk.Button(
            container,
            text="Export Patients",
            command=self.export_patients,
        )
        self.export_button.grid(row=row, column=1, sticky="w", pady=(10, 12))

        row += 1
        ttk.Label(container, textvariable=self.status_var, foreground="#1f4e79").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        row += 1
        log_frame = ttk.LabelFrame(container, text="Export Log", padding=10)
        log_frame.grid(row=row, column=0, columnspan=2, sticky="nsew")
        container.rowconfigure(row, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=18, state="disabled")
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
        state = "disabled" if busy else "normal"
        self.connect_button.configure(state=state)
        self.export_button.configure(state=state)
        if not busy:
            self.visit_type_combo.configure(state="readonly")

    def browse_output(self) -> None:
        visit_type_name = self.visit_type_var.get().strip()
        if visit_type_name:
            initial_name = sanitize_filename(visit_type_name) + ".csv"
        else:
            initial_name = "openmrs_export.csv"
        selected = filedialog.asksaveasfilename(
            title="Choose export file",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=initial_name,
        )
        if selected:
            self.output_var.set(selected)

    def _create_api(self) -> ApiClient:
        base_url = normalize_base_url(self.base_url_var.get())
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            raise ValueError("Username and password are required.")
        api = ApiClient(base_url=base_url, username=username, password=password)
        try:
            api.session_ok = api.login_session()
        except Exception:
            api.session_ok = False
            api.get_json(f"{api.base_url}/session")
        return api

    def load_visit_types(self) -> None:
        if self.export_in_progress:
            return

        def worker() -> None:
            self.root.after(
                0,
                lambda: self.status_var.set("Connecting to OpenMRS and loading visit types..."),
            )
            self.root.after(0, lambda: self.set_busy(True))
            try:
                api = self._create_api()
                visit_types = sorted(
                    api.get_visit_types(),
                    key=lambda item: str(item.get("name", "")).lower(),
                )
                visit_type_names = [
                    visit_type.get("name", "") for visit_type in visit_types if visit_type.get("name")
                ]
                if not visit_type_names:
                    raise RuntimeError("No visit types were returned by this OpenMRS server.")

                def on_success() -> None:
                    self.api = api
                    self.visit_types = visit_types
                    self.visit_type_combo["values"] = visit_type_names
                    self.visit_type_var.set(visit_type_names[0])
                    self.status_var.set(f"Connected. Loaded {len(visit_type_names)} visit types.")
                    self.log(f"Connected to {api.base_url}")
                    self.log(f"Loaded {len(visit_type_names)} visit types from OpenMRS.")
                    self.set_busy(False)

                self.root.after(0, on_success)
            except Exception as exc:
                self.root.after(
                    0,
                    lambda exc=exc: self._handle_error("Connection failed", exc),
                )

        threading.Thread(target=worker, daemon=True).start()

    def export_patients(self) -> None:
        if self.export_in_progress:
            return

        try:
            start_date = normalize_date_filter(self.start_date_var.get())
            end_date = normalize_date_filter(self.end_date_var.get())
            validate_date_range(start_date, end_date)
        except ValueError as exc:
            messagebox.showerror("Invalid date", str(exc))
            return

        visit_type_name = self.visit_type_var.get().strip()
        if not visit_type_name:
            messagebox.showerror(
                "Visit type required",
                "Load visit types and choose one before exporting.",
            )
            return

        output_path = Path(self.output_var.get().strip())
        if not output_path.name:
            messagebox.showerror(
                "Output file required",
                "Choose where to save the CSV export.",
            )
            return

        org_unit_code = self.username_var.get().strip()
        program_value = determine_program_from_visit_type(visit_type_name)

        def worker() -> None:
            self.export_in_progress = True
            self.root.after(0, lambda: self.set_busy(True))
            self.root.after(0, lambda: self.status_var.set("Export in progress..."))
            try:
                api = self.api or self._create_api()
                if not self.visit_types:
                    self.visit_types = api.get_visit_types()

                selected_visit = next(
                    (
                        visit_type
                        for visit_type in self.visit_types
                        if visit_type.get("name") == visit_type_name
                    ),
                    None,
                )
                if not selected_visit:
                    raise RuntimeError(
                        f"Visit type '{visit_type_name}' was not found on the current server."
                    )

                self.root.after(
                    0,
                    lambda: self.log(
                        f"Loading visits for '{visit_type_name}'"
                        + (f" from {start_date}" if start_date else "")
                        + (f" to {end_date}" if end_date else "")
                        + "..."
                    ),
                )

                visits = api.get_visits(start_date, end_date, page_size=100)
                patients = get_patients_by_visit_type(
                    visits=visits,
                    visit_type_uuid=selected_visit["uuid"],
                    visit_start_date=start_date,
                    visit_end_date=end_date,
                )
                if not patients:
                    raise RuntimeError(
                        "No patients matched the selected visit type and date range."
                    )

                self.root.after(
                    0,
                    lambda: self.log(
                        f"Matched {len(patients)} patients with visit type '{visit_type_name}'. "
                        "Their full observations, diagnoses, medications, and orders will be merged into the export."
                    ),
                )

                exported_count = write_patients_csv(
                    api=api,
                    patients=patients,
                    output_filename=output_path,
                    org_unit_code=org_unit_code,
                    program_value=program_value,
                    fetch_concurrency=4,
                )

                def on_success() -> None:
                    self.api = api
                    self.status_var.set(
                        f"Export complete. {exported_count} patients written."
                    )
                    self.log(
                        f"Export finished: {exported_count} patients written to {output_path}"
                    )
                    self.set_busy(False)
                    self.export_in_progress = False
                    messagebox.showinfo(
                        "Export complete",
                        f"Exported {exported_count} patients to:\n{output_path}",
                    )

                self.root.after(0, on_success)
            except Exception as exc:
                self.root.after(
                    0,
                    lambda exc=exc: self._handle_error("Export failed", exc),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _handle_error(self, title: str, exc: Exception) -> None:
        self.status_var.set(f"{title}: {exc}")
        self.log(f"{title}: {exc}")
        self.set_busy(False)
        self.export_in_progress = False
        messagebox.showerror(title, str(exc))


def main() -> None:
    root = tk.Tk()
    app = ExportApp(root)
    app.log(
        "This exporter filters patients by the selected visit type and date range, then merges the patient's "
        "available observations, diagnoses, medications, and orders into one CSV row."
    )
    app.log(
        "Observation columns are labeled with the outermost observation form/template concept set when it can "
        "be inferred from the grouped obs data, for example 'BP [Maternal History]'."
    )
    root.mainloop()


if __name__ == "__main__":
    main()