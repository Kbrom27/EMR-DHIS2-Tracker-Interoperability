from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from o3app.config import HEADER_SEPARATOR
from o3app.utils import deduplicate, normalize_label

INVESTIGATION_STAGE = "Investigation sheet"

CBC_GROUP = "CBC n"
RENAL_GROUP = "Renal function n"
ELECTROLYTE_GROUP = "Serum electrolyte"
LIVER_GROUP = "Liver function n"


def _header(data_element_name: str) -> str:
    return f"{INVESTIGATION_STAGE}{HEADER_SEPARATOR}{data_element_name}"


NEONATAL_INVESTIGATION_HEADERS = [
    _header(name)
    for name in (
        "Date and Time inv n",
        "RBS inv n",
        "CBC n",
        "WBC inv n",
        "Hg inv n",
        "HCT inv n",
        "PLT inv n",
        "ANC inv n",
        "ESR inv n",
        "CRP inv n",
        "Renal function n",
        "BUN inv n",
        "CR inv n",
        "Serum electrolyte",
        "K inv n",
        "Na inv n",
        "Ca inv n",
        "Cl inv n",
        "P inv n",
        "Liver function n",
        "ALP inv n",
        "Direct Bilirubin inv n",
        "Indirect Bilirubin inv n",
        "Total Bilirubin inv n",
        "ALT n",
        "AST n",
        "Blood culture inv n",
        "Cranial u/s inv n",
        "Abdominal u/s inv n",
        "CXR inv n",
        "Echo inv n",
        "other inv n",
    )
]

GROUPED_LAB_TESTS: Dict[str, Tuple[str, str]] = {
    "WBC inv n": (CBC_GROUP, "1"),
    "Hg inv n": (CBC_GROUP, "2"),
    "HCT inv n": (CBC_GROUP, "3"),
    "PLT inv n": (CBC_GROUP, "4"),
    "ANC inv n": (CBC_GROUP, "5"),
    "BUN inv n": (RENAL_GROUP, "1"),
    "CR inv n": (RENAL_GROUP, "2"),
    "K inv n": (ELECTROLYTE_GROUP, "1"),
    "Na inv n": (ELECTROLYTE_GROUP, "2"),
    "Ca inv n": (ELECTROLYTE_GROUP, "3"),
    "Cl inv n": (ELECTROLYTE_GROUP, "4"),
    "P inv n": (ELECTROLYTE_GROUP, "5"),
    "ALP inv n": (LIVER_GROUP, "1"),
    "Direct Bilirubin inv n": (LIVER_GROUP, "2"),
    "Indirect Bilirubin inv n": (LIVER_GROUP, "3"),
    "Total Bilirubin inv n": (LIVER_GROUP, "4"),
    "ALT n": (LIVER_GROUP, "5"),
    "AST n": (LIVER_GROUP, "6"),
}

LAB_TEST_SYNONYMS: Dict[str, List[str]] = {
    "WBC inv n": [
        "wbc",
        "white blood cell",
        "white blood cells",
        "white blood count",
        "leukocyte",
        "leukocytes",
        "leucocyte",
        "leucocytes",
    ],
    "Hg inv n": ["hg", "hb", "haemoglobin", "hemoglobin", "haemoglobin concentration"],
    "HCT inv n": ["hct", "haematocrit", "hematocrit", "pcv", "packed cell volume"],
    "PLT inv n": ["plt", "platelet", "platelets", "platelet count"],
    "ANC inv n": ["anc", "absolute neutrophil count"],
    "BUN inv n": ["bun", "blood urea nitrogen", "urea"],
    "CR inv n": ["cr", "creatinine", "serum creatinine"],
    "K inv n": ["k", "potassium"],
    "Na inv n": ["na", "sodium"],
    "Ca inv n": ["ca", "calcium"],
    "Cl inv n": ["cl", "chloride"],
    "P inv n": ["p", "phosphorus", "phosphate", "serum phosphorus"],
    "ALP inv n": ["alp", "alkaline phosphatase", "alkaline phosphotase"],
    "Direct Bilirubin inv n": ["direct bilirubin", "conjugated bilirubin"],
    "Indirect Bilirubin inv n": ["indirect bilirubin", "unconjugated bilirubin"],
    "Total Bilirubin inv n": ["total bilirubin", "serum bilirubin", "bilirubin total"],
    "ALT n": ["alt", "alanine aminotransferase", "alanine transaminase", "sgpt"],
    "AST n": ["ast", "aspartate aminotransferase", "aspartate transaminase", "sgot"],
}

LAB_PANEL_SYNONYMS: Dict[str, List[str]] = {
    CBC_GROUP: ["complete blood count", "cbc", "full blood count", "fbc", "hemogram"],
}

TEXT_LAB_TESTS: Dict[str, List[str]] = {
    "Blood culture inv n": ["blood culture"],
    "Cranial u/s inv n": [
        "cranial ultrasound",
        "cranial us",
        "cranial ultrasonography",
        "transfontanelle ultrasound",
        "transfontanelle us",
    ],
    "Abdominal u/s inv n": [
        "abdominal ultrasound",
        "abdominal us",
        "abdominal ultrasonography",
    ],
    "CXR inv n": ["cxr", "chest x ray", "chest x-ray", "chest xray", "chest radiograph"],
    "Echo inv n": ["echo", "echocardiography", "echocardiogram"],
}

STANDALONE_LAB_TESTS: Dict[str, List[str]] = {
    "RBS inv n": ["rbs", "random blood sugar", "random blood glucose", "blood sugar random"],
    "ESR inv n": ["esr", "erythrocyte sedimentation rate"],
    "CRP inv n": ["crp", "c reactive protein", "c-reactive protein"],
}

_ALL_SYNONYMS: Dict[str, List[str]] = {
    **LAB_TEST_SYNONYMS,
    **TEXT_LAB_TESTS,
    **STANDALONE_LAB_TESTS,
}

_SYNONYM_TO_TEST: Dict[str, str] = {}
for _test_name, _synonyms in _ALL_SYNONYMS.items():
    for _synonym in _synonyms:
        _SYNONYM_TO_TEST[normalize_label(_synonym)] = _test_name

_PANEL_LOOKUP: Dict[str, str] = {}
for _group_name, _synonyms in LAB_PANEL_SYNONYMS.items():
    for _synonym in _synonyms:
        _PANEL_LOOKUP[normalize_label(_synonym)] = _group_name


def parse_lab_entries(raw_value: Optional[str]) -> List[str]:
    entries: List[str] = []
    for part in str(raw_value or "").split(" | "):
        entry = str(part or "").strip()
        if not entry:
            continue
        cleaned_tokens = [
            token
            for token in entry.split()
            if not re.match(r"^(instructions|comment):", token, re.IGNORECASE)
        ]
        cleaned = " ".join(cleaned_tokens).strip()
        if cleaned:
            entries.append(cleaned)
    return entries


def build_investigation_values(lab_entries: List[str]) -> Dict[str, str]:
    group_codes: Dict[str, List[str]] = {}
    text_values: Dict[str, List[str]] = {}
    other_values: List[str] = []

    for entry in lab_entries:
        normalized = normalize_label(entry)
        if not normalized:
            continue

        panel_group = _PANEL_LOOKUP.get(normalized)
        if panel_group:
            for _test, (group, code) in GROUPED_LAB_TESTS.items():
                if group == panel_group and code not in group_codes.setdefault(group, []):
                    group_codes[group].append(code)
            continue

        test_name = _SYNONYM_TO_TEST.get(normalized)
        if not test_name:
            other_values.append(entry)
            continue

        if test_name in GROUPED_LAB_TESTS:
            group, code = GROUPED_LAB_TESTS[test_name]
            if code not in group_codes.setdefault(group, []):
                group_codes[group].append(code)
        elif test_name in TEXT_LAB_TESTS:
            text_values.setdefault(test_name, []).append(entry)
        else:
            other_values.append(entry)

    values: Dict[str, str] = {}
    for group, codes in group_codes.items():
        values[_header(group)] = ";".join(sorted(codes, key=int))
    for test_name, entries in text_values.items():
        values[_header(test_name)] = "; ".join(deduplicate(entries))
    if other_values:
        values[_header("other inv n")] = "; ".join(deduplicate(other_values))
    return values


def apply_neonatal_investigation_transform(
    transformed_row: Dict[str, str],
    source_row: Dict[str, str],
) -> None:
    lab_entries = parse_lab_entries(source_row.get("lab_results", ""))
    if not lab_entries:
        return
    for header, value in build_investigation_values(lab_entries).items():
        if header in transformed_row:
            transformed_row[header] = value
