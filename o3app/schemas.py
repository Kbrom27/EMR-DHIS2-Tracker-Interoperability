from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Answer:
    concept: str
    label: str


@dataclass
class Question:
    label: str
    concept: str
    rendering: str
    answers: List[Answer] = field(default_factory=list)


@dataclass
class Form:
    name: str
    uuid: str
    encounter_type: str
    questions: List[Question] = field(default_factory=list)


def _extract_questions(node: dict, questions: List[Question]) -> None:
    raw_questions = node.get("questions")
    if isinstance(raw_questions, list):
        for raw in raw_questions:
            if not isinstance(raw, dict):
                continue
            question_type = str(raw.get("type") or "").strip()
            options = raw.get("questionOptions")
            concept = ""
            rendering = ""
            answers: List[Answer] = []
            if isinstance(options, dict):
                concept = str(options.get("concept") or "").strip()
                rendering = str(options.get("rendering") or "").strip()
                raw_answers = options.get("answers")
                if isinstance(raw_answers, list):
                    for raw_answer in raw_answers:
                        if not isinstance(raw_answer, dict):
                            continue
                        answers.append(
                            Answer(
                                concept=str(raw_answer.get("concept") or "").strip(),
                                label=str(raw_answer.get("label") or "").strip(),
                            )
                        )
            if not concept:
                continue
            questions.append(
                Question(
                    label=str(raw.get("label") or "").strip(),
                    concept=concept,
                    rendering=rendering,
                    answers=answers,
                )
            )
            _extract_questions(raw, questions)
    for section in node.get("sections") or []:
        if isinstance(section, dict):
            _extract_questions(section, questions)


def load_form_file(path: Path) -> Optional[Form]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None

    name = str(data.get("name") or "").strip()
    if not name:
        return None

    questions: List[Question] = []
    for page in data.get("pages") or []:
        if isinstance(page, dict):
            _extract_questions(page, questions)

    return Form(
        name=name,
        uuid=str(data.get("uuid") or "").strip(),
        encounter_type=str(data.get("encounterType") or "").strip(),
        questions=questions,
    )


class FormRegistry:
    def __init__(self, forms: List[Form]) -> None:
        self.forms = forms
        self._form_by_uuid: Dict[str, Form] = {}
        self._forms_by_encounter_type: Dict[str, List[Form]] = {}
        self._concept_label: Dict[str, str] = {}
        self._concept_form: Dict[str, str] = {}

        uuid_owners: Dict[str, str] = {}
        for form in forms:
            if form.uuid:
                uuid_owners.setdefault(form.uuid, form.name)
                if uuid_owners[form.uuid] != form.name:
                    uuid_owners[form.uuid] = ""
        for form in forms:
            if form.uuid and uuid_owners.get(form.uuid):
                self._form_by_uuid[form.uuid] = form
            if form.encounter_type:
                self._forms_by_encounter_type.setdefault(form.encounter_type, []).append(form)
            for question in form.questions:
                if question.concept and question.concept not in self._concept_label:
                    self._concept_label[question.concept] = question.label
                self._concept_form.setdefault(question.concept, form.name)

    def form_name_for_uuid(self, form_uuid: str) -> str:
        form = self._form_by_uuid.get(form_uuid or "")
        return form.name if form else ""

    def form_name_for_encounter_type(self, encounter_type_uuid: str) -> str:
        matches = self._forms_by_encounter_type.get(encounter_type_uuid or "")
        if not matches or len(matches) != 1:
            return ""
        return matches[0].name

    def concept_label(self, concept_uuid: str) -> str:
        return self._concept_label.get(concept_uuid or "", "")


def load_forms_from_directories(directories: List[Path]) -> FormRegistry:
    forms: List[Form] = []
    for directory in directories:
        if not directory or not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            form = load_form_file(path)
            if form is not None:
                forms.append(form)
    return FormRegistry(forms)


def load_forms_recursive(directory: Path) -> FormRegistry:
    forms: List[Form] = []
    if not directory or not directory.is_dir():
        return FormRegistry(forms)
    for path in sorted(directory.rglob("*.json")):
        form = load_form_file(path)
        if form is not None:
            forms.append(form)
    return FormRegistry(forms)


DEFAULT_MATERNAL_SCHEMA_DIR = "Maternal Inpatient Data"
DEFAULT_NEONATAL_SCHEMA_DIR = "Neonatal Care Form"


def load_default_forms(schema_root: Path) -> FormRegistry:
    if not schema_root:
        return FormRegistry([])
    return load_forms_from_directories(
        [
            schema_root / DEFAULT_MATERNAL_SCHEMA_DIR,
            schema_root / DEFAULT_NEONATAL_SCHEMA_DIR,
        ]
    )
