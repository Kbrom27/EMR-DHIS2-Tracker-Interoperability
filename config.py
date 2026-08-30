from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

RESOURCES_DIR = Path(__file__).resolve().with_name("Resources")

O3_SCHEMA_ROOT = RESOURCES_DIR / "O3" / "Schemas"
O3_METADATA_PATH = RESOURCES_DIR / "O3" / "metadata_for_openmrs_3x.json"

MATERNAL_PROGRAM = "Maternal Inpatient Data/aLoraiFNkng"
NEONATAL_PROGRAM = "Neonatal Care Form/QYJKpoUeg9F"
PROGRAM_LABELS = (MATERNAL_PROGRAM, NEONATAL_PROGRAM)

SPECIAL_COLUMNS = ["org_unit", "program", "Record ID"]
CONTEXT_COLUMNS = ["visit_date"]
HEADER_SEPARATOR = " :: "
BLANK_MARKERS = {"", "none", "null", "nan", "n/a"}
STOPWORDS = {"a", "an", "at", "for", "in", "n", "of", "on", "the", "to"}

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

DEFAULT_PROGRAM_SPECS = {
    MATERNAL_PROGRAM: {
        "mapping_path": RESOURCES_DIR / "EMR-DHIS2 Tracker Maternal Mapping.xlsx",
        "dictionary_path": RESOURCES_DIR / "MID data disctionary.xlsx",
    },
    NEONATAL_PROGRAM: {
        "mapping_path": RESOURCES_DIR / "EMR-DHIS2 Tracker Neonatal Mapping.xlsx",
        "dictionary_path": RESOURCES_DIR / "NCF data disctionary.xlsx",
    },
}

PROGRAM_SPECS: Dict[str, Dict[str, Path]] = dict(DEFAULT_PROGRAM_SPECS)

FACILITIES = (
    ("Adama Teaching Hospital", "ADMT"),
    ("Olenchity Primary Hospital", "OLC"),
    ("Meki Primary Hospital", "MKP"),
    ("Batu Primary Hospital", "BT"),
    ("Adare GH", "ADR"),
    ("Tula Primary Hospital", "TUL"),
    ("Karamara Primary Hospital", "KRM"),
    ("Dubti General Hospital", "DUB"),
    ("Axum referral hospital", "AxRH"),
    ("Mulu Assefa Primary hospital", "MASPH"),
    ("Boru Meda GH", "BRM"),
    ("Debre Birhan CSH", "DBR"),
    ("Test", "Test"),
)

FACILITY_CODES = dict(FACILITIES)

STAGE_NAME_ALIASES = {
    "Medication sheet": "Medication and intervention",
    "Neonatal Medication Adminstration Sheet": "Medication and intervention",
    "Neonatal Intervention Sheet": "Medication and intervention",
    "Neonatal Discharge care form": "Discharge care form",
    "Neonatal Nurse followup Sheet": "Nurse followup Sheet",
}


def normalize_stage_name(name: str) -> str:
    cleaned = str(name or "").strip()
    return STAGE_NAME_ALIASES.get(cleaned, cleaned)

MATERNAL_COMPUTED_DIAGNOSIS_HEADERS = (
    "Diagnosis :: Obstetric complications",
    "Diagnosis :: Amniotic fluid abnormalities",
    "Diagnosis :: Obstetric complications Others",
)

DIAGNOSIS_OBSTETRIC_COMPLICATIONS_HEADER = "Diagnosis :: Obstetric complications"
DIAGNOSIS_AMNIOTIC_FLUID_HEADER = "Diagnosis :: Amniotic fluid abnormalities"
DIAGNOSIS_OBSTETRIC_COMPLICATIONS_OTHER_HEADER = "Diagnosis :: Obstetric complications Others"

MATERNAL_DIAGNOSIS_SOURCE_HEADERS = ("diagnoses",)

DIAGNOSIS_METADATA_VALUES = {
    "primary", "secondary", "confirmed", "presumed", "false", "true",
}


def normalize_program_value(value: str) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    lower = cleaned.casefold()
    if "aloraifnkng" in lower or "maternal inpatient data" in lower:
        return MATERNAL_PROGRAM
    if "qyjkpoueg9f" in lower or "neonatal care form" in lower:
        return NEONATAL_PROGRAM
    return cleaned


def is_patient_eligible_for_program(gender: str, age_val: Optional[Any], program_value: str) -> bool:
    import re
    norm_program = normalize_program_value(program_value)
    norm_gender = str(gender or "").strip().casefold()

    parsed_age: Optional[float] = None
    if age_val is not None:
        cleaned_age = str(age_val).strip()
        match = re.search(r"^\d+(\.\d+)?", cleaned_age)
        if match:
            try:
                parsed_age = float(match.group(0))
            except ValueError:
                parsed_age = None

    if norm_program == MATERNAL_PROGRAM:
        if norm_gender not in ("f", "female"):
            return False
        if parsed_age is not None and parsed_age < 10:
            return False
        return True

    if norm_program == NEONATAL_PROGRAM:
        if parsed_age is not None and parsed_age > 0:
            return False
        return True

    return True
