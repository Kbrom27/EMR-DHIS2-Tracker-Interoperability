from __future__ import annotations

from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from config import HEADER_SEPARATOR
from models import HeaderInfo, MappingField
from rules.tracker_mapping_rules import (
    get_preferred_source_headers,
    uses_strict_preferred_sources,
)
from utils import (
    build_header_info,
    deduplicate,
    extract_bracket_label,
    find_exact_header,
    normalize_label,
    strip_bracket_suffix,
    token_signature,
)


FIELD_SOURCE_ALIASES = {
    "neonate first name": "first_name",
    "neonate last name": "family_name",
    "neonate mrn": "patient_id",
    "neonate sex": "gender",
}


def score_header_match(
    candidate: str,
    header: HeaderInfo,
    stage_name: str,
    form_name: str,
    has_explicit_source: bool,
) -> float:
    candidate_base = strip_bracket_suffix(candidate)
    candidate_norm = normalize_label(candidate_base)
    if not candidate_norm:
        return 0.0

    candidate_tokens = token_signature(candidate_base)
    score = 0.0

    if header.normalized_header == candidate_norm or header.normalized_base == candidate_norm:
        score = 1.0
    elif candidate_tokens and header.base_tokens == candidate_tokens:
        score = 0.97
    elif candidate_tokens and set(candidate_tokens).issubset(set(header.base_tokens)):
        score = 0.91
    else:
        score = SequenceMatcher(None, candidate_norm, header.normalized_base).ratio()

    if form_name and header.source_label:
        score += 0.03 * SequenceMatcher(None, normalize_label(form_name), header.source_label).ratio()
    if stage_name and header.source_label:
        score += 0.02 * SequenceMatcher(None, normalize_label(stage_name), header.source_label).ratio()
    if has_explicit_source:
        score += 0.01

    return score


def resolve_alias_source_header(field: MappingField, header_info: Sequence[HeaderInfo]) -> str:
    alias = FIELD_SOURCE_ALIASES.get(normalize_label(field.data_element_name))
    if not alias:
        return ""
    return find_exact_header(alias, header_info)


def resolve_source_header(field: MappingField, header_info: Sequence[HeaderInfo]) -> str:
    preferred_candidates = deduplicate(get_preferred_source_headers(field.target_header))
    strict_preferred_sources = uses_strict_preferred_sources(field.target_header)
    for candidate in preferred_candidates:
        exact = find_exact_header(candidate, header_info)
        if exact:
            return exact
    if strict_preferred_sources:
        return ""

    preferred_best_header = ""
    preferred_best_score = 0.0
    for candidate in preferred_candidates:
        for header in header_info:
            score = score_header_match(
                candidate=candidate,
                header=header,
                stage_name=field.stage_name,
                form_name=field.form_name,
                has_explicit_source=True,
            )
            if score > preferred_best_score:
                preferred_best_score = score
                preferred_best_header = header.header
    if preferred_best_score >= 0.82:
        return preferred_best_header

    alias_header = resolve_alias_source_header(field, header_info)
    if alias_header:
        return alias_header

    candidates = deduplicate(
        value
        for value in (
            field.source_name,
            field.form_name,
            field.data_element_name,
        )
        if value
    )

    for candidate in candidates:
        exact = find_exact_header(candidate, header_info)
        if exact:
            return exact

    best_header = ""
    best_score = 0.0
    for candidate in candidates:
        for header in header_info:
            score = score_header_match(
                candidate=candidate,
                header=header,
                stage_name=field.stage_name,
                form_name=field.form_name,
                has_explicit_source=bool(field.source_name),
            )
            if score > best_score:
                best_score = score
                best_header = header.header

    threshold = 0.82 if field.source_name else 0.90
    return best_header if best_score >= threshold else ""


def select_mapping_field(
    fields: Sequence[MappingField],
    org_unit: str,
    target_header: str,
) -> Optional[MappingField]:
    candidates = [
        field for field in fields if field.target_header == target_header and field.source_header
    ]
    if not candidates:
        return None

    normalized_org = str(org_unit or "").strip().casefold()
    normalized_org_label = normalize_label(org_unit)
    if normalized_org:
        for field in candidates:
            if (
                field.org_unit.casefold() == normalized_org
                or normalize_label(field.org_unit) == normalized_org_label
            ):
                return field

    for field in candidates:
        if not field.org_unit:
            return field

    return candidates[0]


def resolve_program_sources(
    program_fields: Dict[str, List[MappingField]],
    export_headers: Sequence[str],
    programs: Optional[Sequence[str]] = None,
) -> Tuple[Dict[str, List[MappingField]], Dict[str, List[str]]]:
    header_info = build_header_info(export_headers)
    resolved_fields: Dict[str, List[MappingField]] = {}
    missing_fields: Dict[str, List[str]] = {}

    selected_programs = programs or tuple(program_fields.keys())
    for program in selected_programs:
        fields = program_fields[program]
        resolved: List[MappingField] = []
        missing: List[str] = []
        for field in fields:
            source_header = resolve_source_header(field, header_info)
            resolved_field = MappingField(
                stage_name=field.stage_name,
                data_element_name=field.data_element_name,
                target_header=field.target_header,
                source_name=field.source_name,
                form_name=field.form_name,
                data_type=field.data_type,
                options_text=field.options_text,
                org_unit=field.org_unit,
                source_header=source_header,
            )
            if source_header:
                resolved.append(resolved_field)
            else:
                missing.append(resolved_field.target_header)
        resolved_fields[program] = resolved
        missing_fields[program] = missing

    return resolved_fields, missing_fields
