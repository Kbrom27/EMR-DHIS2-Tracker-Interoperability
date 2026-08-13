from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class AttributeField:
    header: str
    attribute_id: str
    attribute_name: str
    data_type: str
    options_text: str


@dataclass
class StageField:
    header: str
    stage_name: str
    stage_id: str
    data_element_id: str
    data_element_name: str
    data_type: str
    options_text: str


@dataclass
class ProgramConfig:
    program_label: str
    program_uid: str
    tracked_entity_type: str
    record_id_attribute_id: str
    attributes: Dict[str, AttributeField]
    stages: Dict[str, List[StageField]]


@dataclass
class ImportValueIssue:
    record_id: str
    patient: str
    program: str
    stage: str
    column: str
    field_name: str
    field_id: str
    value: str
    reason: str


@dataclass
class DictionaryField:
    stage_name: str
    data_element_name: str
    data_element_id: str
    form_name: str
    data_type: str
    options_text: str


@dataclass
class MappingField:
    stage_name: str
    data_element_name: str
    target_header: str
    source_name: str
    form_name: str
    data_type: str
    options_text: str
    org_unit: str = ""
    source_header: str = ""


@dataclass(frozen=True)
class HeaderInfo:
    header: str
    base_name: str
    normalized_header: str
    normalized_base: str
    header_tokens: Tuple[str, ...]
    base_tokens: Tuple[str, ...]
    source_label: str


@dataclass(frozen=True)
class Option:
    label: str


@dataclass(frozen=True)
class ConceptOption:
    label: str


@dataclass(frozen=True)
class Concept:
    name: str
    short_name: str
    uuid: str
    data_type: str
    options: Tuple[ConceptOption, ...]


@dataclass(frozen=True)
class MappingTarget:
    program: str
    org_unit: str
    stage_name: str
    data_element_name: str
    data_element_id: str
    target_header: str
    source_concept_name: str
    source_concept_uuid: str
    dhis2_options_text: str
    dhis2_options: Tuple[Option, ...]
    source_options: Tuple[ConceptOption, ...]
