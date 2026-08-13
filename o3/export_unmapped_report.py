from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from config import MATERNAL_PROGRAM, NEONATAL_PROGRAM
from o3.mappings import (
    DEFAULT_NEONATAL_DICTIONARY,
    MATERNAL_SCHEMA_DIR,
    NEONATAL_SCHEMA_DIR,
    load_dictionary_fields,
    match_data_element,
    match_stage_name,
    strip_data_element_disambiguators,
    write_xlsx_sheets,
)
from o3.schemas import Form, load_forms_from_directories
from utils import normalize_label, read_xlsx_rows, row_to_dict

PROGRAM_DETAILS = {
    NEONATAL_PROGRAM: {
        "program_name": "Neonatal Care Form",
        "schema_subdir": NEONATAL_SCHEMA_DIR,
        "dictionary_path": DEFAULT_NEONATAL_DICTIONARY,
    },
    MATERNAL_PROGRAM: {
        "program_name": "Maternal Inpatient Data",
        "schema_subdir": MATERNAL_SCHEMA_DIR,
        "dictionary_path": None,
    },
}

SUMMARY_HEADERS = [
    "Program Stage",
    "Live DEs",
    "Dictionary DEs",
    "Unmapped Live DEs",
    "O3 Questions",
    "Mapped Questions",
    "Unmapped Questions",
]

UNMAPPED_DE_HEADERS = [
    "Program Stage",
    "Data Element ID",
    "Data Element Name",
    "Value Type",
    "In Dictionary",
]

UNMAPPED_QUESTION_HEADERS = [
    "Form Name",
    "Resolved Stage",
    "Question Label",
    "Rendering",
    "Matched Data Element",
    "Reason",
]


def _load_live_metadata(path: Path) -> Dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    stages_by_id = {stage["id"]: stage for stage in data.get("programStages", [])}
    elements_by_id = {element["id"]: element for element in data.get("dataElements", [])}
    return {"programs": data.get("programs", []), "stages": stages_by_id, "elements": elements_by_id}


def _find_program_stages(metadata: Dict[str, object], program_name: str) -> List[Tuple[str, str, List[Dict[str, str]]]]:
    program = next(
        (item for item in metadata["programs"] if item.get("name") == program_name),
        None,
    )
    if program is None:
        raise RuntimeError(f"Program {program_name!r} was not found in the live DHIS2 metadata.")
    stages: List[Tuple[str, str, List[Dict[str, str]]]] = []
    for stage_ref in program.get("programStages", []):
        stage = metadata["stages"].get(stage_ref.get("id", ""))
        if stage is None:
            continue
        elements = []
        for psde in stage.get("programStageDataElements", []):
            element_id = psde.get("dataElement", {}).get("id", "")
            element = metadata["elements"].get(element_id)
            if element is None:
                continue
            elements.append(
                {
                    "id": element_id,
                    "name": str(element.get("name", "")),
                    "value_type": str(element.get("valueType", "")),
                }
            )
        stages.append((str(stage.get("name", "")), stage_ref.get("id", ""), elements))
    return stages


def _load_dictionary_de_index(dictionary_path: Path) -> Tuple[set, set]:
    rows = read_xlsx_rows(dictionary_path)
    if not rows:
        raise RuntimeError(f"No rows were found in {dictionary_path.name}.")
    headers = rows[0]
    ids: set = set()
    names: set = set()
    for row in rows[1:]:
        item = row_to_dict(row, headers)
        de_id = item.get("Data Element ID", "").strip()
        de_name = item.get("Data Element Name", "").strip()
        if de_id:
            ids.add(de_id)
        if de_name:
            names.add(normalize_label(de_name))
            names.add(normalize_label(strip_data_element_disambiguators(de_name)))
    return ids, names


def _dictionary_name_match(name: str, names: set) -> bool:
    return (
        normalize_label(name) in names
        or normalize_label(strip_data_element_disambiguators(name)) in names
    )


def _stage_dictionary_counts(
    dictionary_stages: Dict[str, Dict[str, Dict[str, str]]],
) -> Dict[str, int]:
    return {stage_name: len(elements) for stage_name, elements in dictionary_stages.items()}


def _resolve_dictionary_stage(stage_name: str, dictionary_stages: Dict[str, Dict[str, Dict[str, str]]]) -> str:
    for candidate in dictionary_stages:
        if normalize_label(candidate) == normalize_label(stage_name):
            return candidate
    return match_stage_name(stage_name, dictionary_stages)


def _collect_unmapped_questions(
    forms: Sequence[Form],
    dictionary_stages: Dict[str, Dict[str, Dict[str, str]]],
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for form in forms:
        stage_name = match_stage_name(form.name, dictionary_stages)
        stage_elements = dictionary_stages.get(stage_name, {})
        for question in form.questions:
            if not question.label:
                continue
            data_element_name, _field = match_data_element(question.label, stage_elements)
            if data_element_name:
                continue
            if not stage_name:
                reason = "No matching DHIS2 stage in the dictionary"
            else:
                reason = "Stage matched but no matching data element in the dictionary"
            rows.append(
                {
                    "form_name": form.name,
                    "stage_name": stage_name,
                    "question_label": question.label,
                    "rendering": question.rendering,
                    "data_element_name": "",
                    "reason": reason,
                }
            )
    return rows


def export_unmapped_report(
    live_metadata_path: Path,
    schema_root: Path,
    program_value: str,
    output_path: Path,
    dictionary_path: Path | None = None,
) -> Dict[str, object]:
    details = PROGRAM_DETAILS.get(program_value)
    if details is None:
        raise RuntimeError(f"Unsupported program value: {program_value}")
    dictionary_path = dictionary_path or details["dictionary_path"]
    if dictionary_path is None or not dictionary_path.is_file():
        raise RuntimeError(f"Matching dictionary not available for {program_value}.")

    metadata = _load_live_metadata(live_metadata_path)
    dictionary_stages = load_dictionary_fields(dictionary_path)
    dict_ids, dict_names = _load_dictionary_de_index(dictionary_path)

    forms = load_forms_from_directories([schema_root / details["schema_subdir"]]).forms

    summary_rows: List[Sequence[str]] = []
    unmapped_de_rows: List[Sequence[str]] = []
    unmapped_question_rows: List[Sequence[str]] = []

    for stage_name, _stage_id, elements in _find_program_stages(
        metadata, details["program_name"]
    ):
        dictionary_stage = _resolve_dictionary_stage(stage_name, dictionary_stages)
        dictionary_de_count = len(dictionary_stages.get(dictionary_stage, {}))
        live_de_count = len(elements)

        unmapped_elements: List[Dict[str, str]] = []
        for element in elements:
            in_dictionary = (
                element["id"] in dict_ids or _dictionary_name_match(element["name"], dict_names)
            )
            if not in_dictionary:
                unmapped_elements.append(element)
                unmapped_de_rows.append(
                    [
                        stage_name,
                        element["id"],
                        element["name"],
                        element["value_type"],
                        "No",
                    ]
                )
            else:
                unmapped_de_rows.append(
                    [
                        stage_name,
                        element["id"],
                        element["name"],
                        element["value_type"],
                        "Yes",
                    ]
                )

        stage_questions = [
            question
            for form in forms
            if match_stage_name(form.name, dictionary_stages) == dictionary_stage
            for question in form.questions
        ]
        stage_mapped = sum(
            1
            for question in stage_questions
            if match_data_element(
                question.label, dictionary_stages.get(dictionary_stage, {})
            )[0]
        )

        summary_rows.append(
            [
                stage_name,
                live_de_count,
                dictionary_de_count,
                len(unmapped_elements),
                len(stage_questions),
                stage_mapped,
                len(stage_questions) - stage_mapped,
            ]
        )

    for row in _collect_unmapped_questions(forms, dictionary_stages):
        unmapped_question_rows.append(
            [
                row["form_name"],
                row["stage_name"],
                row["question_label"],
                row["rendering"],
                row["data_element_name"],
                row["reason"],
            ]
        )

    write_xlsx_sheets(
        output_path,
        [
            ("Summary", [SUMMARY_HEADERS] + summary_rows),
            ("Unmapped Live DEs", [UNMAPPED_DE_HEADERS] + unmapped_de_rows),
            ("Unmapped O3 Questions", [UNMAPPED_QUESTION_HEADERS] + unmapped_question_rows),
        ],
    )

    return {
        "output_path": str(output_path),
        "stages": len(summary_rows),
        "live_de_count": sum(int(row[1]) for row in summary_rows),
        "dictionary_de_count_stage_match": sum(int(row[2]) for row in summary_rows),
        "unmapped_live_de_count": sum(int(row[3]) for row in summary_rows),
        "unmapped_question_count": len(unmapped_question_rows),
    }


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Export unmapped items to Excel for the O3 workflow.")
    parser.add_argument("--metadata", required=True, help="Live DHIS2 metadata JSON file.")
    parser.add_argument("--schema-root", required=True, help="Folder containing the O3 form schema subfolders.")
    parser.add_argument("--dictionary", help="Local DHIS2 dictionary xlsx (defaults to the program dictionary).")
    parser.add_argument("--program", choices=["neonatal", "maternal"], default="neonatal")
    parser.add_argument("--output", help="Output xlsx path.")
    args = parser.parse_args()

    program_value = (
        NEONATAL_PROGRAM if args.program == "neonatal" else MATERNAL_PROGRAM
    )
    output_path = Path(args.output or Path("O3 Export") / f"{args.program.title()} Unmapped Report.xlsx")
    result = export_unmapped_report(
        live_metadata_path=Path(args.metadata),
        schema_root=Path(args.schema_root),
        dictionary_path=Path(args.dictionary) if args.dictionary else None,
        program_value=program_value,
        output_path=output_path,
    )
    print(
        f"Unmapped report written to {result['output_path']}\n"
        f"  stages: {result['stages']}\n"
        f"  live DEs: {result['live_de_count']} | unmapped live DEs: {result['unmapped_live_de_count']}\n"
        f"  unmapped O3 questions: {result['unmapped_question_count']}"
    )


if __name__ == "__main__":
    main()