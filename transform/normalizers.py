from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from config import BLANK_MARKERS, HEADER_SEPARATOR
from rules.tracker_mapping_rules import (
    apply_field_alias,
    get_external_field_transform,
    get_field_transform,
    resolve_configured_option_value,
    resolve_external_value_mapping,
    should_suppress_value,
)
from utils import (
    blank_to_empty,
    deduplicate,
    normalize_label,
    normalize_token,
    normalized_tokens,
    token_signature,
)


def split_export_values(raw_value: str) -> List[str]:
    text = str(raw_value or "").strip()
    if not text:
        return []
    parts = [part.strip() for part in text.split(" | ")]
    return [part for part in parts if part]


def last_export_value(raw_value: str) -> str:
    values = split_export_values(raw_value)
    return values[-1] if values else blank_to_empty(raw_value)


def parse_options(options_text: str) -> Tuple[Dict[str, str], Dict[str, str], List[Tuple[Tuple[str, ...], str]]]:
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
        canonical = label or code
        if not canonical:
            continue
        if code:
            code_map[code.casefold()] = canonical
            label_map[code.casefold()] = canonical
        label_map[normalize_label(code)] = canonical
        label_map[canonical.casefold()] = canonical
        label_map[normalize_label(canonical)] = canonical
        token_map.append((token_signature(canonical), canonical))
    return code_map, label_map, token_map


def normalize_boolean_token(value: str) -> Optional[str]:
    normalized = normalize_label(value)
    if normalized in {"1", "true", "t", "yes", "y"}:
        return "true"
    if normalized in {"0", "false", "f", "no", "n"}:
        return "false"
    return None


def resolve_option_name(
    part: str,
    code_map: Dict[str, str],
    label_map: Dict[str, str],
    token_map: Sequence[Tuple[Tuple[str, ...], str]],
) -> str:
    exact_code = code_map.get(part.casefold())
    if exact_code:
        return exact_code

    exact_label = label_map.get(part.casefold())
    if exact_label:
        return exact_label

    normalized = normalize_label(part)
    mapped = label_map.get(normalized)
    if mapped:
        return mapped

    boolean_value = normalize_boolean_token(part)
    if boolean_value == "true" and "1" in code_map:
        return code_map["1"]
    if boolean_value == "false" and "0" in code_map:
        return code_map["0"]

    part_tokens = set(token_signature(part))
    best_label = ""
    best_score = 0.0
    part_normalized = normalize_label(part)
    for option_tokens, option_label in token_map:
        if not option_tokens:
            continue
        option_token_set = set(option_tokens)
        if part_tokens and option_token_set.issubset(part_tokens):
            score = 0.9 + (len(option_token_set) / max(len(part_tokens), 1)) * 0.05
        else:
            score = SequenceMatcher(None, part_normalized, normalize_label(option_label)).ratio()
        if score > best_score:
            best_score = score
            best_label = option_label

    return best_label if best_score >= 0.84 else ""


def normalize_option_value(
    raw_value: str,
    data_type: str,
    options_text: str,
    target_header: str,
) -> str:
    code_map, label_map, token_map = parse_options(options_text)
    parts = split_export_values(raw_value)
    if not parts:
        return ""

    multi_value = data_type == "MULTI_TEXT"
    mapped_values: List[str] = []

    for part in parts if multi_value else [parts[-1]]:
        configured = resolve_configured_option_value(
            raw_value=part,
            options_text=options_text,
            target_header=target_header,
            return_codes=False,
        )
        if configured:
            mapped_values.append(configured)
            continue

        mapped = resolve_option_name(part, code_map, label_map, token_map)
        if mapped:
            mapped_values.append(mapped)
            continue

        mapped_values.append(part.strip())

    mapped_values = deduplicate(value for value in mapped_values if value)
    if not mapped_values:
        return ""
    if multi_value:
        return ";".join(mapped_values)
    return mapped_values[-1]


def normalize_integer(raw_value: str) -> str:
    value = last_export_value(raw_value).replace(",", "")
    if not value:
        return ""
    try:
        decimal_value = Decimal(value)
    except InvalidOperation:
        fallback = re.sub(r"[^0-9+-]", "", value)
        if fallback and fallback not in {"+", "-"}:
            return fallback
        return value
    return str(int(decimal_value))


def normalize_number(raw_value: str) -> str:
    value = last_export_value(raw_value).replace(",", "")
    if not value:
        return ""
    try:
        decimal_value = Decimal(value)
    except InvalidOperation:
        fallback = re.sub(r"[^0-9+-.]", "", value)
        if fallback and fallback not in {"+", "-", ".", "+.", "-."}:
            value = fallback
            try:
                decimal_value = Decimal(value)
            except InvalidOperation:
                return value
        else:
            return value
    normalized = format(decimal_value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def normalize_date(raw_value: str) -> str:
    from datetime import datetime

    value = last_export_value(raw_value)
    if not value:
        return ""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", value)
    if match:
        return match.group(1)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    month_match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)[a-z]*"
        r"\s+\d{1,2}[;,]?\s+\d{4}",
        value,
        re.IGNORECASE,
    )
    if month_match:
        try:
            return datetime.strptime(month_match.group(0).replace(";", " "), "%B %d %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value


def normalize_time(raw_value: str) -> str:
    value = last_export_value(raw_value)
    if not value:
        return ""
    match = re.search(r"(?:T|\b)(\d{1,2}:\d{2})(?::\d{2})?", value)
    if match:
        hour, minute = match.group(1).split(":")
        return f"{int(hour):02d}:{minute}"
    return value


def normalize_datetime_value(raw_value: str) -> str:
    value = last_export_value(raw_value)
    if not value:
        return ""
    date_value = normalize_date(value)
    time_value = normalize_time(value)
    if date_value and time_value:
        return f"{date_value}T{time_value}:00"
    return value


def normalize_text_value(raw_value: str) -> str:
    return last_export_value(raw_value)


def _is_configured_option(value: str, options_text: str) -> bool:
    if not options_text:
        return False
    code_map, label_map, _ = parse_options(options_text)
    raw = value.strip()
    normalized = normalize_label(raw)
    return (
        raw.casefold() in code_map
        or raw.casefold() in label_map
        or normalized in label_map
    )


def normalize_tracker_value(
    raw_value: str,
    data_type: str,
    options_text: str,
    target_header: str = "",
    program: str = "",
) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if value.casefold() in BLANK_MARKERS and not _is_configured_option(value, options_text):
        return ""
    if should_suppress_value(value, target_header):
        return ""

    external_value = resolve_external_value_mapping(value, target_header, program)
    if external_value:
        return external_value

    configured_transform = get_external_field_transform(target_header, program) or get_field_transform(target_header)
    if configured_transform == "date":
        return normalize_date(value)
    if configured_transform == "time":
        return normalize_time(value)
    if configured_transform == "datetime":
        return normalize_datetime_value(value)
    if configured_transform == "all_text":
        return value

    if options_text:
        return normalize_option_value(value, data_type, options_text, target_header)

    if data_type in {"BOOLEAN", "TRUE_ONLY"}:
        normalized = normalize_boolean_token(value)
        if data_type == "TRUE_ONLY":
            return "true" if normalized == "true" else ""
        return normalized or last_export_value(value)

    if data_type in {"INTEGER", "INTEGER_ZERO_OR_POSITIVE", "INTEGER_POSITIVE", "INTEGER_NEGATIVE"}:
        return normalize_integer(value)

    if data_type in {"NUMBER", "PERCENTAGE", "UNIT_INTERVAL"}:
        return normalize_number(value)

    if data_type == "DATE":
        return normalize_date(value)

    if data_type == "TIME":
        return normalize_time(value)

    if data_type == "DATETIME":
        return normalize_datetime_value(value)

    configured_text = apply_field_alias(normalize_text_value(value), target_header)
    if configured_text:
        return configured_text

    return normalize_text_value(value)
