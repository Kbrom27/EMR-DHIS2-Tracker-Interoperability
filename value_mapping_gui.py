from __future__ import annotations

import csv
import re
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Iterable, List, Sequence, Tuple

from tracker_mapping_rules import parse_ordered_options
from transform_export_to_dhis2_csv import (
    HEADER_SEPARATOR,
    blank_to_empty,
    find_mapping_header,
    is_mapping_source_column,
    read_xlsx_rows,
    row_to_dict,
    source_column_org_unit,
    strip_bracket_suffix,
)


RESOURCES_DIR = Path(__file__).resolve().with_name("Resources")
DEFAULT_DICTIONARY = RESOURCES_DIR / "MID data disctionary.xlsx"
DEFAULT_MAPPING = RESOURCES_DIR / "EMR-DHIS2 Tracker Maternal Mapping.xlsx"
DEFAULT_CONCEPTS = RESOURCES_DIR / "Tula Openmrs Concepts.csv"
DEFAULT_VALUE_MAPPINGS = RESOURCES_DIR / "EMR-DHIS2 Tracker Value Mappings.csv"

VALUE_MAPPING_COLUMNS = [
    "program",
    "target_header",
    "stage_name",
    "data_element_name",
    "source_value",
    "dhis2_value",
    "transform",
    "notes",
    "org_unit",
    "source_concept_name",
    "source_concept_uuid",
]

BLANK_MARKERS = {"", "none", "null", "nan", "n/a"}


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


def normalize_text(value: object) -> str:
    text = (
        str(value or "")
        .replace("\u2019", "'")
        .replace("`", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .strip()
        .lower()
    )
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def clean_concept_name(value: object) -> str:
    return strip_bracket_suffix(str(value or "")).strip()


def split_options(value: str) -> List[str]:
    options: List[str] = []
    for option in str(value or "").split(";"):
        option = option.strip()
        if option and option.casefold() not in BLANK_MARKERS:
            options.append(option)
    return options


def program_from_path(path: Path) -> str:
    name = path.name.casefold()
    if "ncf" in name or "neonatal" in name:
        return "Neonatal Care Form"
    if "mid" in name or "maternal" in name:
        return "Maternal Inpatient Data"
    return ""


def read_dictionary_options(path: Path) -> Dict[Tuple[str, str], Dict[str, str]]:
    rows = read_xlsx_rows(path)
    if not rows:
        raise RuntimeError(f"No rows were found in {path.name}.")

    headers = rows[0]
    fields: Dict[Tuple[str, str], Dict[str, str]] = {}
    for row in rows[1:]:
        item = row_to_dict(row, headers)
        stage_name = blank_to_empty(item.get("Stage Name"))
        data_element_name = blank_to_empty(item.get("Data Element Name"))
        options_text = blank_to_empty(item.get("Options"))
        if not stage_name or not data_element_name or not options_text:
            continue
        fields[(stage_name, data_element_name)] = {
            "stage_name": stage_name,
            "data_element_name": data_element_name,
            "data_element_id": blank_to_empty(item.get("Data Element ID")),
            "options_text": options_text,
        }
    return fields


def read_mapping_rows(path: Path) -> List[Dict[str, str]]:
    rows = read_xlsx_rows(path)
    if not rows:
        raise RuntimeError(f"No rows were found in {path.name}.")

    headers = rows[0]
    stage_header = find_mapping_header(
        headers,
        ("DHIS2 Program Stage Name", "Program Stage Name", "Stage Name"),
    )
    data_element_header = find_mapping_header(
        headers,
        ("DHIS2 Data Element Name", "Data Element Name"),
    )
    source_headers = [
        header
        for header in headers
        if is_mapping_source_column(header, stage_header, data_element_header)
    ]
    if not stage_header or not data_element_header or not source_headers:
        raise RuntimeError(
            f"{path.name} must contain stage, DHIS2 data element, and EMR concept columns."
        )

    mapping_rows: List[Dict[str, str]] = []
    for row in rows[1:]:
        item = row_to_dict(row, headers)
        stage_name = blank_to_empty(item.get(stage_header))
        data_element_name = blank_to_empty(item.get(data_element_header))
        if not stage_name or not data_element_name:
            continue
        for source_header in source_headers:
            source_concept = blank_to_empty(item.get(source_header))
            if not source_concept:
                continue
            mapping_rows.append(
                {
                    "stage_name": stage_name,
                    "data_element_name": data_element_name,
                    "source_concept_name": source_concept,
                    "org_unit": source_column_org_unit(source_header),
                }
            )
    return mapping_rows


def read_concepts(path: Path) -> Dict[str, Concept]:
    concepts: List[Concept] = []

    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            name = blank_to_empty(row.get("EMR Concept Fully Specified Name"))
            short_name = blank_to_empty(row.get("EMR Concept short name"))
            uuid = blank_to_empty(row.get("EMR Concept UUID"))
            data_type = blank_to_empty(row.get("EMR concept Data Type"))
            raw_options = split_options(blank_to_empty(row.get("EMR Options (Answers)")))
            concept = Concept(
                name=name,
                short_name=short_name,
                uuid=uuid,
                data_type=data_type,
                options=tuple(ConceptOption(label=option) for option in raw_options),
            )
            concepts.append(concept)

    concepts_by_key: Dict[str, Concept] = {}
    for concept in concepts:
        for label in (concept.name, concept.short_name):
            key = normalize_text(clean_concept_name(label))
            if key:
                concepts_by_key.setdefault(key, concept)
    return concepts_by_key


def build_targets(
    dictionary_path: Path,
    mapping_path: Path,
    concepts_path: Path,
) -> List[MappingTarget]:
    dictionary_fields = read_dictionary_options(dictionary_path)
    mapping_rows = read_mapping_rows(mapping_path)
    concepts_by_key = read_concepts(concepts_path)
    program = program_from_path(dictionary_path) or program_from_path(mapping_path)

    targets: List[MappingTarget] = []
    seen = set()
    for mapping_row in mapping_rows:
        dictionary_field = dictionary_fields.get(
            (mapping_row["stage_name"], mapping_row["data_element_name"])
        )
        if not dictionary_field:
            continue

        concept_key = normalize_text(clean_concept_name(mapping_row["source_concept_name"]))
        concept = concepts_by_key.get(concept_key)
        if concept is None:
            continue

        key = (
            mapping_row["org_unit"],
            dictionary_field["stage_name"],
            dictionary_field["data_element_name"],
            concept.uuid,
        )
        if key in seen:
            continue
        seen.add(key)

        target_header = (
            f"{dictionary_field['stage_name']}{HEADER_SEPARATOR}"
            f"{dictionary_field['data_element_name']}"
        )
        dhis2_options = tuple(
            Option(label=option["label"])
            for option in parse_ordered_options(dictionary_field["options_text"])
        )
        targets.append(
            MappingTarget(
                program=program,
                org_unit=mapping_row["org_unit"],
                stage_name=dictionary_field["stage_name"],
                data_element_name=dictionary_field["data_element_name"],
                data_element_id=dictionary_field["data_element_id"],
                target_header=target_header,
                source_concept_name=concept.name,
                source_concept_uuid=concept.uuid,
                dhis2_options_text=dictionary_field["options_text"],
                dhis2_options=dhis2_options,
                source_options=concept.options,
            )
        )

    targets.sort(
        key=lambda item: (
            item.stage_name.casefold(),
            item.data_element_name.casefold(),
            item.org_unit.casefold(),
            item.source_concept_name.casefold(),
        )
    )
    return targets


def read_value_mappings(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    if not path.exists():
        return [], VALUE_MAPPING_COLUMNS.copy()

    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = [
            fieldname
            for fieldname in list(reader.fieldnames or [])
            if fieldname not in {"source_option_uuid", "dhis2_option_code"}
        ]
        rows = [{key: blank_to_empty(value) for key, value in row.items()} for row in reader]

    for column in VALUE_MAPPING_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)
    return rows, fieldnames


def write_value_mappings(path: Path, rows: Sequence[Dict[str, str]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in fieldnames})


def mapping_identity(row: Dict[str, str]) -> Tuple[str, str, str, str, str]:
    target_header = row.get("target_header", "")
    if not target_header and row.get("stage_name") and row.get("data_element_name"):
        target_header = f"{row['stage_name']}{HEADER_SEPARATOR}{row['data_element_name']}"
    return (
        normalize_text(row.get("program", "")),
        normalize_text(target_header),
        normalize_text(row.get("org_unit", "")),
        normalize_text(row.get("source_value", "")),
        normalize_text(row.get("dhis2_value", "")),
    )


def existing_rows_for_target(
    rows: Iterable[Dict[str, str]],
    target: MappingTarget,
) -> List[Dict[str, str]]:
    target_key = normalize_text(target.target_header)
    program_key = normalize_text(target.program)
    org_key = normalize_text(target.org_unit)
    matched = []
    for row in rows:
        row_target = row.get("target_header", "")
        if not row_target and row.get("stage_name") and row.get("data_element_name"):
            row_target = f"{row['stage_name']}{HEADER_SEPARATOR}{row['data_element_name']}"
        if normalize_text(row_target) != target_key:
            continue
        row_program = normalize_text(row.get("program", ""))
        if row_program and program_key and row_program != program_key:
            continue
        row_org = normalize_text(row.get("org_unit", ""))
        if row_org and org_key and row_org != org_key:
            continue
        row_concept = normalize_text(row.get("source_concept_uuid") or row.get("source_concept_name", ""))
        target_concept = normalize_text(target.source_concept_uuid or target.source_concept_name)
        if row_concept and target_concept and row_concept != target_concept:
            continue
        if row.get("source_value") and row.get("dhis2_value"):
            matched.append(row)
    return matched


def target_identity(target: MappingTarget) -> Tuple[str, str, str, str]:
    return (
        normalize_text(target.program),
        normalize_text(target.target_header),
        normalize_text(target.org_unit),
        normalize_text(target.source_concept_uuid or target.source_concept_name),
    )


def row_matches_target_scope(row: Dict[str, str], target: MappingTarget) -> bool:
    row_target = row.get("target_header", "")
    if not row_target and row.get("stage_name") and row.get("data_element_name"):
        row_target = f"{row['stage_name']}{HEADER_SEPARATOR}{row['data_element_name']}"

    if normalize_text(row_target) != normalize_text(target.target_header):
        return False

    row_program = normalize_text(row.get("program", ""))
    target_program = normalize_text(target.program)
    if row_program and target_program and row_program != target_program:
        return False

    if normalize_text(row.get("org_unit", "")) != normalize_text(target.org_unit):
        return False

    row_concept = normalize_text(row.get("source_concept_uuid") or row.get("source_concept_name", ""))
    target_concept = normalize_text(target.source_concept_uuid or target.source_concept_name)
    if row_concept and target_concept and row_concept != target_concept:
        return False

    return bool(row.get("source_value") and row.get("dhis2_value"))


class ValueMappingApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("EMR-DHIS2 Tracker Value Mapper")
        self.root.geometry("1180x760")

        self.dictionary_var = tk.StringVar(value=str(DEFAULT_DICTIONARY))
        self.mapping_var = tk.StringVar(value=str(DEFAULT_MAPPING))
        self.concepts_var = tk.StringVar(value=str(DEFAULT_CONCEPTS))
        self.output_var = tk.StringVar(value=str(DEFAULT_VALUE_MAPPINGS))
        self.status_var = tk.StringVar(value="Load files to begin mapping coded/text options.")

        self.targets: List[MappingTarget] = []
        self.current_index = -1
        self.saved_rows: List[Dict[str, str]] = []
        self.saved_fieldnames: List[str] = VALUE_MAPPING_COLUMNS.copy()
        self.source_option_vars: Dict[str, tk.BooleanVar] = {}
        self.dhis2_choice_var = tk.StringVar()
        self.extra_source_var = tk.StringVar()
        self.notes_var = tk.StringVar()
        self.filter_unmapped_var = tk.BooleanVar(value=False)
        self.pending_mappings: Dict[Tuple[str, str, str, str], Dict[str, Dict[str, object]]] = {}
        self.active_dhis2_label = ""
        self.loading_target = False

        self._build_ui()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=14)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        paths = ttk.LabelFrame(container, text="Files", padding=10)
        paths.grid(row=0, column=0, sticky="ew")
        paths.columnconfigure(1, weight=1)

        self._path_row(paths, 0, "Data Dictionary", self.dictionary_var, self.browse_dictionary)
        self._path_row(paths, 1, "Mapping Workbook", self.mapping_var, self.browse_mapping)
        self._path_row(paths, 2, "Tula Concepts CSV", self.concepts_var, self.browse_concepts)
        self._path_row(paths, 3, "Value Mappings CSV", self.output_var, self.browse_output)

        controls = ttk.Frame(container)
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        ttk.Button(controls, text="Load", command=self.load_files).pack(side="left")
        ttk.Button(controls, text="Previous", command=self.previous_target).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Next", command=self.next_target).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(
            controls,
            text="Show unmapped only",
            variable=self.filter_unmapped_var,
            command=self.refresh_target_list,
        ).pack(side="left", padx=(18, 0))
        ttk.Button(controls, text="Save All Drafts", command=self.save_all_drafts).pack(side="right")

        ttk.Label(container, textvariable=self.status_var, foreground="#1f4e79").grid(
            row=2, column=0, sticky="w", pady=(0, 8)
        )

        body = ttk.PanedWindow(container, orient=tk.HORIZONTAL)
        body.grid(row=3, column=0, sticky="nsew")

        left = ttk.Frame(body, padding=(0, 0, 10, 0))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.target_list = tk.Listbox(left, height=20, exportselection=False)
        self.target_list.grid(row=0, column=0, sticky="nsew")
        self.target_list.bind("<<ListboxSelect>>", self.on_target_selected)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.target_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.target_list.configure(yscrollcommand=scrollbar.set)
        body.add(left, weight=1)

        right = ttk.Frame(body)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        body.add(right, weight=3)

        self.detail_var = tk.StringVar(value="No target loaded.")
        ttk.Label(right, textvariable=self.detail_var, justify="left").grid(
            row=0, column=0, sticky="ew"
        )

        option_frame = ttk.LabelFrame(right, text="DHIS2 Option", padding=10)
        option_frame.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        option_frame.columnconfigure(0, weight=1)
        self.dhis2_combo = ttk.Combobox(
            option_frame,
            textvariable=self.dhis2_choice_var,
            state="readonly",
        )
        self.dhis2_combo.grid(row=0, column=0, sticky="ew")
        self.dhis2_combo.bind("<<ComboboxSelected>>", self.on_dhis2_option_selected)

        source_frame = ttk.LabelFrame(right, text="Source Options / Values", padding=10)
        source_frame.grid(row=2, column=0, sticky="nsew")
        source_frame.columnconfigure(0, weight=1)
        source_frame.rowconfigure(0, weight=1)
        self.source_canvas = tk.Canvas(source_frame, highlightthickness=0)
        self.source_canvas.grid(row=0, column=0, sticky="nsew")
        source_scrollbar = ttk.Scrollbar(
            source_frame,
            orient="vertical",
            command=self.source_canvas.yview,
        )
        source_scrollbar.grid(row=0, column=1, sticky="ns")
        self.source_canvas.configure(yscrollcommand=source_scrollbar.set)
        self.source_options_frame = ttk.Frame(self.source_canvas)
        self.source_window = self.source_canvas.create_window(
            (0, 0),
            window=self.source_options_frame,
            anchor="nw",
        )
        self.source_options_frame.bind("<Configure>", self._resize_source_canvas)
        self.source_canvas.bind("<Configure>", self._resize_source_window)

        extra_frame = ttk.Frame(right)
        extra_frame.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        extra_frame.columnconfigure(1, weight=1)
        ttk.Label(extra_frame, text="Extra source values").grid(row=0, column=0, sticky="w")
        ttk.Entry(extra_frame, textvariable=self.extra_source_var).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(8, 0),
        )
        ttk.Label(extra_frame, text="Separate many values with ;").grid(
            row=0,
            column=2,
            sticky="w",
            padx=(8, 0),
        )
        ttk.Label(extra_frame, text="Notes").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(extra_frame, textvariable=self.notes_var).grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(8, 0),
            pady=(8, 0),
        )

    def _path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Button(parent, text="Browse", command=command).grid(row=row, column=2, sticky="e", pady=3)

    def browse_dictionary(self) -> None:
        self._browse_open(self.dictionary_var, [("Excel workbooks", "*.xlsx"), ("All files", "*.*")])

    def browse_mapping(self) -> None:
        self._browse_open(self.mapping_var, [("Excel workbooks", "*.xlsx"), ("All files", "*.*")])

    def browse_concepts(self) -> None:
        self._browse_open(self.concepts_var, [("CSV files", "*.csv"), ("All files", "*.*")])

    def browse_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Select value mappings CSV",
            initialdir=str(RESOURCES_DIR),
            initialfile=DEFAULT_VALUE_MAPPINGS.name,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _browse_open(self, variable: tk.StringVar, filetypes: Sequence[Tuple[str, str]]) -> None:
        path = filedialog.askopenfilename(
            title="Select file",
            initialdir=str(RESOURCES_DIR),
            filetypes=filetypes,
        )
        if path:
            variable.set(path)

    def _resize_source_canvas(self, _event=None) -> None:
        self.source_canvas.configure(scrollregion=self.source_canvas.bbox("all"))

    def _resize_source_window(self, event) -> None:
        self.source_canvas.itemconfigure(self.source_window, width=event.width)

    def load_files(self) -> None:
        try:
            dictionary_path = Path(self.dictionary_var.get()).expanduser()
            mapping_path = Path(self.mapping_var.get()).expanduser()
            concepts_path = Path(self.concepts_var.get()).expanduser()
            output_path = Path(self.output_var.get()).expanduser()
            self.targets = build_targets(dictionary_path, mapping_path, concepts_path)
            self.saved_rows, self.saved_fieldnames = read_value_mappings(output_path)
            self.pending_mappings = {}
            self.active_dhis2_label = ""
            self.current_index = 0 if self.targets else -1
            self.refresh_target_list()
            self.show_target(self.current_index)
            mapped_count = sum(1 for target in self.targets if existing_rows_for_target(self.saved_rows, target))
            self.status_var.set(
                f"Loaded {len(self.targets)} option targets. {mapped_count} already have saved mappings."
            )
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            self.status_var.set("Load failed.")

    def refresh_target_list(self) -> None:
        selected_identity = self.targets[self.current_index] if 0 <= self.current_index < len(self.targets) else None
        self.target_list.delete(0, tk.END)
        visible_indices = []
        for index, target in enumerate(self.targets):
            mapped = bool(existing_rows_for_target(self.saved_rows, target))
            drafted = bool(self.pending_mappings.get(target_identity(target)))
            if self.filter_unmapped_var.get() and mapped:
                continue
            visible_indices.append(index)
            marker = "*" if drafted else "x" if mapped else " "
            facility = f" [{target.org_unit}]" if target.org_unit else ""
            self.target_list.insert(
                tk.END,
                f"{marker} {target.stage_name} :: {target.data_element_name}{facility}",
            )
        self.target_list.visible_indices = visible_indices  # type: ignore[attr-defined]
        if selected_identity in self.targets:
            self.current_index = self.targets.index(selected_identity)

    def show_target(self, index: int) -> None:
        self.snapshot_active_selection()
        if not self.targets or index < 0 or index >= len(self.targets):
            self.loading_target = True
            self.detail_var.set("No target loaded.")
            self.dhis2_combo.configure(values=[])
            self.dhis2_choice_var.set("")
            self.clear_source_options()
            self.active_dhis2_label = ""
            self.loading_target = False
            return

        target = self.targets[index]
        self.loading_target = True
        self.current_index = index
        self.ensure_pending_target(target)
        self.detail_var.set(
            "\n".join(
                [
                    f"Target: {target.stage_name} :: {target.data_element_name}",
                    f"Program: {target.program or '(not detected)'}",
                    f"Facility column: {target.org_unit or '(generic)'}",
                    f"Source concept: {target.source_concept_name} ({target.source_concept_uuid})",
                ]
            )
        )
        self.dhis2_combo.configure(
            values=[option.label for option in target.dhis2_options]
        )
        self.dhis2_choice_var.set("")
        self.extra_source_var.set("")
        self.notes_var.set("")
        self.render_source_options(target)
        self.apply_initial_selection(target)
        self.select_visible_index(index)
        self.loading_target = False

    def clear_source_options(self) -> None:
        for child in self.source_options_frame.winfo_children():
            child.destroy()
        self.source_option_vars.clear()

    def render_source_options(self, target: MappingTarget) -> None:
        self.clear_source_options()
        for row, option in enumerate(target.source_options):
            var = tk.BooleanVar(value=False)
            self.source_option_vars[option.label] = var
            ttk.Checkbutton(
                self.source_options_frame,
                text=option.label,
                variable=var,
            ).grid(row=row, column=0, sticky="w", pady=2)
        if not target.source_options:
            ttk.Label(
                self.source_options_frame,
                text="No coded source options were found for this concept. Use extra source values.",
            ).grid(row=0, column=0, sticky="w")

    def ensure_pending_target(self, target: MappingTarget) -> None:
        identity = target_identity(target)
        if identity in self.pending_mappings:
            return

        draft: Dict[str, Dict[str, object]] = {}
        for row in existing_rows_for_target(self.saved_rows, target):
            dhis2_value = row.get("dhis2_value", "")
            source_value = row.get("source_value", "")
            if not dhis2_value or not source_value:
                continue
            item = draft.setdefault(dhis2_value, {"sources": [], "notes": row.get("notes", "")})
            sources = item["sources"]
            if isinstance(sources, list) and source_value not in sources:
                sources.append(source_value)
            if row.get("notes") and not item.get("notes"):
                item["notes"] = row.get("notes", "")
        self.pending_mappings[identity] = draft

    def apply_initial_selection(self, target: MappingTarget) -> None:
        draft = self.pending_mappings.get(target_identity(target), {})
        if draft:
            first_dhis2_value = next(iter(draft.keys()))
            self.dhis2_choice_var.set(first_dhis2_value)
            self.active_dhis2_label = first_dhis2_value
            self.apply_draft_for_selected_dhis2(target)
            return
        self.active_dhis2_label = ""

    def apply_draft_for_selected_dhis2(self, target: MappingTarget) -> None:
        for var in self.source_option_vars.values():
            var.set(False)
        self.extra_source_var.set("")
        self.notes_var.set("")

        dhis2_option = self.selected_dhis2_option()
        if not dhis2_option.label:
            return

        draft = self.pending_mappings.get(target_identity(target), {})
        item = draft.get(dhis2_option.label, {})
        raw_sources = item.get("sources", [])
        sources = raw_sources if isinstance(raw_sources, list) else []
        saved_sources = {normalize_text(source) for source in sources}
        extras = []
        for option in target.source_options:
            if normalize_text(option.label) in saved_sources:
                self.source_option_vars[option.label].set(True)
        known_options = {normalize_text(option.label) for option in target.source_options}
        for source_value in sources:
            if normalize_text(source_value) and normalize_text(source_value) not in known_options:
                extras.append(str(source_value))
        self.extra_source_var.set("; ".join(extras))
        self.notes_var.set(str(item.get("notes") or ""))

    def snapshot_active_selection(self) -> None:
        if self.loading_target or not (0 <= self.current_index < len(self.targets)):
            return
        if not self.active_dhis2_label:
            return

        target = self.targets[self.current_index]
        self.ensure_pending_target(target)
        identity = target_identity(target)
        source_values = self.selected_source_values(target)
        draft = self.pending_mappings.setdefault(identity, {})
        if source_values:
            draft[self.active_dhis2_label] = {
                "sources": [source.label for source in source_values],
                "notes": self.notes_var.get().strip(),
            }
        else:
            draft.pop(self.active_dhis2_label, None)

    def on_dhis2_option_selected(self, _event=None) -> None:
        if self.loading_target or not (0 <= self.current_index < len(self.targets)):
            return
        self.snapshot_active_selection()
        target = self.targets[self.current_index]
        self.active_dhis2_label = self.selected_dhis2_option().label
        self.apply_draft_for_selected_dhis2(target)

    def select_visible_index(self, target_index: int) -> None:
        visible_indices = getattr(self.target_list, "visible_indices", [])
        self.target_list.selection_clear(0, tk.END)
        if target_index in visible_indices:
            list_index = visible_indices.index(target_index)
            self.target_list.selection_set(list_index)
            self.target_list.see(list_index)

    def on_target_selected(self, _event=None) -> None:
        if self.loading_target:
            return
        selection = self.target_list.curselection()
        if not selection:
            return
        visible_indices = getattr(self.target_list, "visible_indices", [])
        if selection[0] < len(visible_indices):
            self.show_target(visible_indices[selection[0]])

    def previous_target(self) -> None:
        if self.current_index > 0:
            self.show_target(self.current_index - 1)

    def next_target(self) -> None:
        if self.current_index + 1 < len(self.targets):
            self.show_target(self.current_index + 1)

    def selected_dhis2_option(self) -> Option:
        selected = self.dhis2_choice_var.get()
        for option in self.targets[self.current_index].dhis2_options:
            if normalize_text(selected) == normalize_text(option.label):
                return option
        if selected:
            return Option(label=selected)
        return Option(label="")

    def selected_source_values(self, target: MappingTarget) -> List[ConceptOption]:
        selected = [
            option
            for option in target.source_options
            if self.source_option_vars.get(option.label) and self.source_option_vars[option.label].get()
        ]
        for extra in split_options(self.extra_source_var.get()):
            selected.append(ConceptOption(label=extra))
        seen = set()
        unique: List[ConceptOption] = []
        for option in selected:
            key = normalize_text(option.label)
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(option)
        return unique

    def save_all_drafts(self) -> None:
        if not self.targets or not (0 <= self.current_index < len(self.targets)):
            return

        self.snapshot_active_selection()
        targets_to_save = [
            target
            for target in self.targets
            if self.pending_mappings.get(target_identity(target))
        ]
        if not targets_to_save:
            messagebox.showwarning(
                "Missing mappings",
                "Map at least one source value to a DHIS2 option before saving.",
            )
            return

        output_path = Path(self.output_var.get()).expanduser()
        rows, fieldnames = read_value_mappings(output_path)
        kept_rows = [
            row
            for row in rows
            if not any(row_matches_target_scope(row, target) for target in targets_to_save)
        ]
        saved_count = 0
        for target in targets_to_save:
            draft = self.pending_mappings.get(target_identity(target), {})
            for dhis2_value, item in draft.items():
                raw_sources = item.get("sources", [])
                sources = raw_sources if isinstance(raw_sources, list) else []
                notes = str(item.get("notes") or "")
                for source_value in sources:
                    kept_rows.append(
                        {
                            "program": target.program,
                            "target_header": target.target_header,
                            "stage_name": target.stage_name,
                            "data_element_name": target.data_element_name,
                            "source_value": str(source_value),
                            "dhis2_value": dhis2_value,
                            "transform": "",
                            "notes": notes,
                            "org_unit": target.org_unit,
                            "source_concept_name": target.source_concept_name,
                            "source_concept_uuid": target.source_concept_uuid,
                        }
                    )
                    saved_count += 1

        for column in VALUE_MAPPING_COLUMNS:
            if column not in fieldnames:
                fieldnames.append(column)
        write_value_mappings(output_path, kept_rows, fieldnames)
        self.saved_rows = kept_rows
        self.saved_fieldnames = list(fieldnames)
        self.pending_mappings = {}
        self.active_dhis2_label = ""
        self.refresh_target_list()
        self.show_target(self.current_index)
        self.status_var.set(
            f"Saved {saved_count} source value mapping(s) across {len(targets_to_save)} target(s)."
        )

    def save_current(self) -> None:
        self.save_all_drafts()


def main() -> None:
    root = tk.Tk()
    ValueMappingApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
