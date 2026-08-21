from __future__ import annotations

import re
from typing import Dict, List, Optional

from config import HEADER_SEPARATOR
from utils import deduplicate, normalize_label

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

GROUPED_LAB_TESTS: Dict[str, tuple[str, str]] = {
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

_NUMERIC_LAB_TESTS = frozenset(GROUPED_LAB_TESTS) | frozenset(STANDALONE_LAB_TESTS)
_NUMBER_PATTERN = re.compile(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)")
_BOOLEAN_PATTERN = re.compile(r"^(true|false|yes|no|t|f|y|n|1|0)$", re.IGNORECASE)


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


def parse_raw_lab_entries(raw_value: Optional[str]) -> List[str]:
    return [part.strip() for part in str(raw_value or "").split(" | ") if part.strip()]


def _is_number(value: object) -> bool:
    return bool(re.fullmatch(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)", str(value or "").strip()))


def _best_matching_test(entry: str) -> str:
    entry_tokens = set(normalize_label(entry).split())
    if not entry_tokens:
        return ""
    best_test = ""
    best_length = 0
    for test_name, synonyms in _ALL_SYNONYMS.items():
        for synonym in synonyms:
            synonym_tokens = set(normalize_label(synonym).split())
            if not synonym_tokens or not synonym_tokens.issubset(entry_tokens):
                continue
            length = sum(len(token) for token in synonym_tokens)
            if length > best_length:
                best_test = test_name
                best_length = length
    return best_test


def _extract_numeric_value(entry: str) -> str:
    match = _NUMBER_PATTERN.search(entry)
    return match.group(0) if match else ""


def _ensure_valid_numeric_values(transformed_row: Dict[str, str]) -> None:
    for test_name in _NUMERIC_LAB_TESTS:
        header = _header(test_name)
        if header not in transformed_row:
            continue
        current = str(transformed_row.get(header, "") or "").strip()
        if current and not _is_number(current):
            transformed_row[header] = ""


def _clear_boolean_text_values(transformed_row: Dict[str, str]) -> None:
    for test_name in TEXT_LAB_TESTS:
        header = _header(test_name)
        if header not in transformed_row:
            continue
        current = str(transformed_row.get(header, "") or "").strip()
        if current and _BOOLEAN_PATTERN.fullmatch(current):
            transformed_row[header] = ""


def _fill_values_from_lab_results(
    transformed_row: Dict[str, str],
    lab_entries: List[str],
) -> List[str]:
    unrecognized: List[str] = []
    for entry in lab_entries:
        normalized = normalize_label(entry)
        if not normalized:
            continue
        if _PANEL_LOOKUP.get(normalized):
            continue
        test_name = _best_matching_test(entry)
        if not test_name:
            unrecognized.append(entry)
            continue
        if test_name not in _NUMERIC_LAB_TESTS:
            continue
        header = _header(test_name)
        if transformed_row.get(header, ""):
            continue
        value = _extract_numeric_value(entry)
        if value and _is_number(value):
            transformed_row[header] = value
    return unrecognized


def _apply_group_codes(transformed_row: Dict[str, str]) -> None:
    group_codes: Dict[str, List[str]] = {}
    for test_name, (group, code) in GROUPED_LAB_TESTS.items():
        header = _header(test_name)
        if transformed_row.get(header, "") and _is_number(transformed_row.get(header, "")):
            group_codes.setdefault(group, []).append(code)
    for group, codes in group_codes.items():
        group_header = _header(group)
        if group_header in transformed_row:
            transformed_row[group_header] = ";".join(sorted(set(codes), key=int))


def _unrecognized_lab_entries(cleaned_entries: List[str]) -> List[str]:
    unrecognized: List[str] = []
    for entry in cleaned_entries:
        normalized = normalize_label(entry)
        if not normalized:
            continue
        if _PANEL_LOOKUP.get(normalized):
            continue
        if _best_matching_test(entry):
            continue
        unrecognized.append(entry)
    return unrecognized


def apply_neonatal_investigation_transform(
    transformed_row: Dict[str, str],
    source_row: Dict[str, str],
) -> None:
    _ensure_valid_numeric_values(transformed_row)
    _clear_boolean_text_values(transformed_row)

    lab_results = str(source_row.get("lab_results", "") or "")
    _fill_values_from_lab_results(transformed_row, parse_raw_lab_entries(lab_results))

    other_header = _header("other inv n")
    if not transformed_row.get(other_header, ""):
        other_values = _unrecognized_lab_entries(parse_lab_entries(lab_results))
        if other_values:
            transformed_row[other_header] = "; ".join(deduplicate(other_values))

    _apply_group_codes(transformed_row)
