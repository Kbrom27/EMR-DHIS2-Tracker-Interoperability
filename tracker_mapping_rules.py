import csv
import re
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple


# Central place for EMR -> DHIS2 tracker overrides.
# Use FIELD_RULES for:
# - preferred_sources: exact or approximate export column names to prefer
# - transform: date, time, or datetime extraction from the chosen source value
# - aliases: source value spelling/code variants mapped to the desired tracker value
#
# For mappings you want to edit without changing Python code, use:
# Resources/EMR-DHIS2 Tracker Value Mappings.csv
FIELD_RULES: Dict[str, Dict[str, object]] = {
    "Delivery summary :: Date of Delivery": {
        "preferred_sources": ["Date and Time of Birth [Delivery Summary]"],
        "transform": "date",
    },
    "Delivery summary :: Time of Delivery:": {
        "preferred_sources": ["Date and Time of Birth [Delivery Summary]"],
        "transform": "time",
    },
    "DM followup chart :: Date insulin": {
        "preferred_sources": ["Date and Time of insulin [DM Follow Up Chart]"],
        "transform": "date",
    },
    "DM followup chart :: Time insulin": {
        "preferred_sources": ["Date and Time of insulin [DM Follow Up Chart]"],
        "transform": "time",
    },
    "TOLAC CHART :: Tolac event date": {
        "preferred_sources": ["Date & Time [VBAC and TOLAC CHART]"],
        "transform": "date",
    },
    "TOLAC CHART :: Date VBAC": {
        "preferred_sources": ["Date & Time [VBAC and TOLAC CHART]"],
        "transform": "date",
    },
    "TOLAC CHART :: Time VBAC": {
        "preferred_sources": ["Date & Time [VBAC and TOLAC CHART]"],
        "transform": "time",
    },
    "APH CHART :: APH event date": {
        "preferred_sources": ["Date & Time [APH Follow up Form]"],
        "transform": "date",
    },
    "APH CHART :: Date APH": {
        "preferred_sources": ["Date & Time [APH Follow up Form]"],
        "transform": "date",
    },
    "APH CHART :: Time APH": {
        "preferred_sources": ["Date & Time [APH Follow up Form]"],
        "transform": "time",
    },
    "Latent Phase followup :: Date LPF": {
        "preferred_sources": ["Date: [Latent Phase of Labor Follow up Chart]"],
        "transform": "date",
    },
    "Latent Phase followup :: Time LPF": {
        "preferred_sources": ["Time: [Latent Phase of Labor Follow up Chart]"],
        "transform": "time",
    },
    "kick chart :: Kick chart event date": {
        "preferred_sources": ["kick count date [Kick Chart]"],
        "transform": "date",
    },
    "Induction and augmentation chart :: Date of initiation IA": {
        "preferred_sources": ["Date and time of initiation [Induction and Augumentation delivery]"],
        "transform": "date",
    },
    "Induction and augmentation chart :: Time of initiation IA": {
        "preferred_sources": ["Date and time of initiation [Induction and Augumentation delivery]"],
        "transform": "time",
    },
    "Induction and augmentation chart :: Date IA": {
        "preferred_sources": ["Date & Time [Induction and Augumentation delivery]"],
        "transform": "date",
    },
    "Induction and augmentation chart :: Time IA": {
        "preferred_sources": ["Date & Time [Induction and Augumentation delivery]"],
        "transform": "time",
    },
    "Induction and augmentation chart :: Induction and Augmentation event date": {
        "preferred_sources": ["Date & Time [Induction and Augumentation delivery]"],
        "transform": "date",
    },
    "Instrumental Delivery form :: Date of instrumental delivery": {
        "preferred_sources": ["Date and time of instrumental delivery [Instrumental Delivery Form]"],
        "transform": "date",
    },
    "Instrumental Delivery form :: Time of instrumental delivery": {
        "preferred_sources": ["Date and time of instrumental delivery [Instrumental Delivery Form]"],
        "transform": "time",
    },
    "Medication Administration record :: Medication Adminstration event date": {
        "preferred_sources": ["Date & Time [PICU Medication Administration Sheet]"],
        "transform": "date",
    },
    "MGSO4 follow up :: MGSO4 Followup event date": {
        "preferred_sources": ["Date & Time [MGSO4 follow up]"],
        "transform": "date",
    },
    "PROM chart :: Date PROM": {
        "preferred_sources": ["Date & Time [PROM Follow up Sheet]"],
        "transform": "date",
    },
    "PROM chart :: Time PROM": {
        "preferred_sources": ["Date & Time [PROM Follow up Sheet]"],
        "transform": "time",
    },
    "PROM chart :: PROM event date": {
        "preferred_sources": ["Date & Time [PROM Follow up Sheet]"],
        "transform": "date",
    },
    "Stillbirth Evaluation :: Date of stillbirth delivery": {
        "preferred_sources": ["Date and time of stillbirth delivery [Stillbirth Evaluation Form]"],
        "transform": "date",
    },
    "Stillbirth Evaluation :: Time stillbirth": {
        "preferred_sources": ["Date and time of stillbirth delivery [Stillbirth Evaluation Form]"],
        "transform": "time",
    },
    "Stillbirth Evaluation :: Stillbirth Evaluation event date": {
        "preferred_sources": [
            "Date & Time [Stillbirth Evaluation Form]",
            "Date and time of stillbirth delivery [Stillbirth Evaluation Form]",
        ],
        "transform": "date",
    },
    "Pain Management :: Date PM": {
        "preferred_sources": ["Date and time? [Maternal Pain Management]"],
        "transform": "date",
    },
    "Pain Management :: Time PM": {
        "preferred_sources": ["Date and time? [Maternal Pain Management]"],
        "transform": "time",
    },
    "Pain Management :: Pain Management event date": {
        "preferred_sources": ["Date and time? [Maternal Pain Management]"],
        "transform": "date",
    },
    "Discharge Summary :: Discharge summary event date": {
        "preferred_sources": ["Date & Time [Maternal Discharge Summary]"],
        "transform": "date",
    },
    "NICU Admission Careform :: Date of delivery": {
        "preferred_sources": [
            "Date and Time of Birth [Neonatal Admission Hx and PE]",
            "Date of birth [Intra-facility Neonatal Referral Form]",
        ],
        "transform": "date",
    },
    "NICU Admission Careform :: Time of birth": {
        "preferred_sources": [
            "Date and Time of Birth [Neonatal Admission Hx and PE]",
            "Time of birth [Neonatal Admission Hx and PE]",
        ],
        "transform": "time",
    },
    "NICU Admission Careform :: Date of Admission n": {
        "preferred_sources": ["Date and Time of Admission? [Neonatal Admission Hx and PE]"],
        "transform": "date",
    },
    "NICU Admission Careform :: Time of Admission n": {
        "preferred_sources": ["Date and Time of Admission? [Neonatal Admission Hx and PE]"],
        "transform": "time",
    },
    "Medication sheet :: Time of Administration": {
        "preferred_sources": ["Date & Time [PICU Medication Administration Sheet]"],
        "transform": "time",
    },
    "Physical Examination :: Lie": {
        "aliases": {
            "Longitudinal": ["Longitudinal lda"],
            "Transverse": ["Transverse lie", "Transverse lsa"],
            "Oblique": ["Oblique lie"],
        }
    },
    "Delivery summary :: HIV Result": {
        "aliases": {
            "+ve": ["VCT, Reactive", "Reactive", "Positive", "Positive."],
            "-Ve": ["VCT, Non-Reactive", "Non-Reactive", "Negative", "Negative."],
        }
    },
    "Delivery summary :: Laceration Repair": {
        "aliases": {
            "1st degree": ["First Degree Perineal Laceration, with Delivery", "First degree"],
            "2nd degree": ["Second Degree Perineal Laceration, with Delivery", "Second degree"],
            "3rd degree": ["Third Degree Perineal Laceration, with Delivery", "Third degree"],
            "None": ["No laceration", "None noted"],
        }
    },
    "Delivery summary :: birth outcome": {"aliases": {"Alive": ["HEIF Alive"]}},
    "Delivery summary :: birth outcome newborn 2": {"aliases": {"Alive": ["HEIF Alive"]}},
    "Delivery summary :: birth outcome newborn 3": {"aliases": {"Alive": ["HEIF Alive"]}},
    "Delivery summary :: birth outcome newborn 4": {"aliases": {"Alive": ["HEIF Alive"]}},
}

RESOURCES_DIR = Path(__file__).resolve().with_name("Resources")
VALUE_MAPPING_PATH = RESOURCES_DIR / "EMR-DHIS2 Tracker Value Mappings.csv"
PROGRAM_VALUE_MAPPING_PATHS = {
    "maternal inpatient data": RESOURCES_DIR / "EMR-DHIS2 Tracker Maternal Value Mappings.csv",
    "neonatal care form": RESOURCES_DIR / "EMR-DHIS2 Tracker Neonatal Value Mappings.csv",
}
SUPPORTED_EXTERNAL_TRANSFORMS = {"date", "time", "datetime"}
_EXTERNAL_VALUE_RULES_CACHE: Dict[Path, Tuple[Optional[float], List[Dict[str, str]]]] = {}


INFERRED_OPTION_ALIASES: Dict[FrozenSet[str], Dict[str, Sequence[str]]] = {
    frozenset({"yes", "no"}): {
        "yes": ("1", "true", "t", "yes", "y"),
        "no": ("0", "2", "false", "f", "no", "n"),
    },
    frozenset({"true", "false"}): {
        "true": ("1", "true", "t", "yes", "y"),
        "false": ("0", "2", "false", "f", "no", "n"),
    },
    frozenset({"present", "absent"}): {
        "present": ("1", "true", "t", "yes", "y", "present"),
        "absent": ("0", "2", "false", "f", "no", "n", "absent"),
    },
    frozenset({"male", "female"}): {
        "male": ("1", "male", "m", "boy"),
        "female": ("2", "female", "f", "girl"),
    },
    frozenset({"positive", "negative"}): {
        "positive": ("1", "positive", "pos", "reactive"),
        "negative": ("2", "0", "negative", "neg", "non reactive", "non-reactive"),
    },
    frozenset({"alive", "fresh", "macerated"}): {
        "alive": ("1", "alive", "live birth", "heif alive"),
        "fresh": ("2", "fresh"),
        "macerated": ("3", "macerated"),
    },
    frozenset({"single", "multiple"}): {
        "single": ("1", "single", "singleton"),
        "multiple": ("2", "multiple", "twin", "twins"),
    },
}


def normalize_rule_text(value: object) -> str:
    text = (
        str(value or "")
        .replace("â€™", "'")
        .replace("`", "'")
        .replace("â€œ", '"')
        .replace("â€", '"')
        .replace("–", "-")
        .strip()
        .lower()
    )
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_program_key(value: object) -> str:
    return normalize_rule_text(str(value or "").split("/", 1)[0])


def normalize_target_key(value: object) -> str:
    return normalize_rule_text(value)


def value_mapping_path_for_program(program: str = "") -> Path:
    program_key = normalize_program_key(program)
    return PROGRAM_VALUE_MAPPING_PATHS.get(program_key, VALUE_MAPPING_PATH)


def load_external_value_rules(program: str = "") -> List[Dict[str, str]]:
    global _EXTERNAL_VALUE_RULES_CACHE

    value_mapping_path = value_mapping_path_for_program(program)
    if not value_mapping_path.exists() and value_mapping_path != VALUE_MAPPING_PATH:
        value_mapping_path = VALUE_MAPPING_PATH

    if not value_mapping_path.exists():
        _EXTERNAL_VALUE_RULES_CACHE[value_mapping_path] = (None, [])
        return []

    modified_time = value_mapping_path.stat().st_mtime
    cached_modified_time, cached_rules = _EXTERNAL_VALUE_RULES_CACHE.get(
        value_mapping_path,
        (None, []),
    )
    if cached_modified_time == modified_time:
        return cached_rules

    with value_mapping_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rules: List[Dict[str, str]] = []
        for row in reader:
            target_header = str(row.get("target_header") or "").strip()
            stage_name = str(row.get("stage_name") or "").strip()
            data_element_name = str(row.get("data_element_name") or "").strip()
            if not target_header and stage_name and data_element_name:
                target_header = f"{stage_name} :: {data_element_name}"

            source_value = str(row.get("source_value") or "").strip()
            dhis2_value = str(row.get("dhis2_value") or "").strip()
            transform = str(row.get("transform") or "").strip().lower()

            if not target_header:
                continue
            if not source_value and not dhis2_value and transform not in SUPPORTED_EXTERNAL_TRANSFORMS:
                continue

            rules.append(
                {
                    "program": str(row.get("program") or "").strip(),
                    "target_header": target_header,
                    "source_value": source_value,
                    "dhis2_value": dhis2_value,
                    "transform": transform,
                }
            )
    _EXTERNAL_VALUE_RULES_CACHE[value_mapping_path] = (modified_time, rules)
    return rules


def external_rule_matches(rule: Dict[str, str], target_header: str, program: str = "") -> bool:
    rule_program = normalize_program_key(rule.get("program", ""))
    if rule_program and rule_program != normalize_program_key(program):
        return False
    return normalize_target_key(rule.get("target_header", "")) == normalize_target_key(target_header)


def get_external_field_transform(target_header: str, program: str = "") -> str:
    for rule in load_external_value_rules(program):
        if not external_rule_matches(rule, target_header, program):
            continue
        transform = str(rule.get("transform") or "").strip().lower()
        if transform in SUPPORTED_EXTERNAL_TRANSFORMS and not rule.get("source_value"):
            return transform
    return ""


def resolve_external_value_mapping(
    raw_value: str,
    target_header: str,
    program: str = "",
) -> str:
    normalized_value = normalize_rule_text(raw_value)
    if not normalized_value:
        return ""

    for rule in load_external_value_rules(program):
        if not external_rule_matches(rule, target_header, program):
            continue
        source_value = str(rule.get("source_value") or "").strip()
        dhis2_value = str(rule.get("dhis2_value") or "").strip()
        if not source_value or not dhis2_value:
            continue
        if normalized_value == normalize_rule_text(source_value):
            return dhis2_value
    return ""


def parse_ordered_options(options_text: str) -> List[Dict[str, str]]:
    options: List[Dict[str, str]] = []
    for raw_option in str(options_text or "").split(";"):
        option = raw_option.strip()
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
        options.append({"code": code, "label": label or code})
    return options


def get_field_rule(target_header: str) -> Dict[str, object]:
    return FIELD_RULES.get(target_header, {})


def get_preferred_source_headers(target_header: str) -> List[str]:
    rule = get_field_rule(target_header)
    return list(rule.get("preferred_sources", []))


def get_field_transform(target_header: str) -> str:
    rule = get_field_rule(target_header)
    return str(rule.get("transform", "") or "").strip().lower()


def apply_field_alias(value: str, target_header: str) -> str:
    normalized = normalize_rule_text(value)
    if not normalized:
        return ""

    aliases = get_field_rule(target_header).get("aliases", {})
    if not isinstance(aliases, dict):
        return ""

    for canonical, raw_values in aliases.items():
        if normalized == normalize_rule_text(canonical):
            return str(canonical)
        for raw_value in raw_values:
            if normalized == normalize_rule_text(raw_value):
                return str(canonical)
    return ""


def resolve_configured_option_value(
    raw_value: str,
    options_text: str,
    target_header: str,
    return_codes: bool,
) -> str:
    options = parse_ordered_options(options_text)
    if not options:
        return ""

    raw_text = str(raw_value or "").strip()
    if any(
        raw_text.casefold() == str(option.get("code") or "").strip().casefold()
        for option in options
        if option.get("code")
    ):
        return ""

    normalized = normalize_rule_text(raw_value)
    if not normalized:
        return ""

    labels_by_key: Dict[str, Dict[str, str]] = {
        normalize_rule_text(option["label"]): option for option in options
    }

    configured = apply_field_alias(raw_value, target_header)
    if configured:
        option = labels_by_key.get(normalize_rule_text(configured))
        if option:
            return option["code"] if return_codes and option["code"] else option["label"]
        if not return_codes:
            return configured

    inferred_aliases = INFERRED_OPTION_ALIASES.get(frozenset(labels_by_key.keys()))
    if inferred_aliases:
        for canonical_key, raw_aliases in inferred_aliases.items():
            if normalized == canonical_key or normalized in {
                normalize_rule_text(alias) for alias in raw_aliases
            }:
                option = labels_by_key.get(canonical_key)
                if option:
                    return option["code"] if return_codes and option["code"] else option["label"]

    if re.fullmatch(r"[+-]?\d+", raw_text):
        index = int(raw_text)
        if 1 <= index <= len(options):
            option = options[index - 1]
            return option["code"] if return_codes and option["code"] else option["label"]

    return ""
