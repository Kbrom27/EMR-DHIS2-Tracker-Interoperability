from __future__ import annotations

import calendar
import threading
import tkinter as tk
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from export_openmrs_gui import (
    DETAIL_COLUMNS,
    ApiClient,
    build_patient_row,
    clean_csv_cell,
    determine_program_from_visit_type,
    get_patients_by_visit_type,
    normalize_base_url,
    normalize_date_filter,
    sanitize_filename,
    validate_date_range,
    write_patients_csv,
)
from import_dhis2_tracker_csv import (
    Dhis2Client,
    build_attribute_payload,
    build_program_configs,
    build_stage_payloads,
    default_import_log_path,
    extract_row_value,
    format_dhis2_error,
    add_import_value_issue,
    import_rows,
    reference_id,
    today_date,
    write_import_value_log,
)
from transform_export_to_dhis2_csv import (
    MATERNAL_PROGRAM,
    NEONATAL_PROGRAM,
    PROGRAM_SPECS,
    SPECIAL_COLUMNS,
    MATERNAL_COMPUTED_DIAGNOSIS_HEADERS,
    apply_maternal_diagnosis_transform,
    blank_to_empty,
    deduplicate,
    load_program_fields,
    normalize_program_value,
    normalize_tracker_value,
    raise_csv_field_limit,
    resolve_program_sources,
    select_mapping_field,
    transform_rows,
)


APP_TITLE = "EMR-DHIS2 Tracker interoperability"


class CalendarPopup(tk.Toplevel):
    def __init__(self, parent: tk.Misc, target_var: tk.StringVar) -> None:
        super().__init__(parent)
        self.target_var = target_var
        self.title("Select date")
        self.resizable(False, False)
        self.configure(bg="#f8fafc")
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        current = self._initial_date()
        self.year = current.year
        self.month = current.month

        self.header = ttk.Frame(self, padding=(10, 10, 10, 4))
        self.header.pack(fill="x")
        ttk.Button(self.header, text="<", width=3, command=self.previous_month).pack(side="left")
        self.title_var = tk.StringVar()
        ttk.Label(self.header, textvariable=self.title_var, anchor="center", width=20).pack(
            side="left", expand=True
        )
        ttk.Button(self.header, text=">", width=3, command=self.next_month).pack(side="right")

        self.days_frame = ttk.Frame(self, padding=(10, 4, 10, 10))
        self.days_frame.pack(fill="both")
        self.render_calendar()

    def _initial_date(self) -> date:
        raw = self.target_var.get().strip()
        if raw:
            try:
                return datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                pass
        return date.today()

    def previous_month(self) -> None:
        self.month -= 1
        if self.month < 1:
            self.month = 12
            self.year -= 1
        self.render_calendar()

    def next_month(self) -> None:
        self.month += 1
        if self.month > 12:
            self.month = 1
            self.year += 1
        self.render_calendar()

    def render_calendar(self) -> None:
        for child in self.days_frame.winfo_children():
            child.destroy()

        self.title_var.set(f"{calendar.month_name[self.month]} {self.year}")
        for column, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            ttk.Label(self.days_frame, text=name, anchor="center", width=5).grid(
                row=0, column=column, padx=1, pady=1
            )

        for row_index, week in enumerate(calendar.monthcalendar(self.year, self.month), start=1):
            for column, day in enumerate(week):
                if not day:
                    ttk.Label(self.days_frame, text="", width=5).grid(
                        row=row_index, column=column, padx=1, pady=1
                    )
                    continue
                ttk.Button(
                    self.days_frame,
                    text=str(day),
                    width=5,
                    command=lambda selected_day=day: self.select_day(selected_day),
                ).grid(row=row_index, column=column, padx=1, pady=1)

    def select_day(self, day: int) -> None:
        self.target_var.set(f"{self.year:04d}-{self.month:02d}-{day:02d}")
        self.destroy()


class DatePicker(ttk.Frame):
    def __init__(self, parent: tk.Misc, textvariable: tk.StringVar) -> None:
        super().__init__(parent)
        self.textvariable = textvariable
        self.entry = ttk.Entry(self, textvariable=textvariable, width=14, state="readonly")
        self.entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(self, text="Select", command=self.open_calendar).grid(
            row=0, column=1, padx=(6, 0)
        )
        ttk.Button(self, text="Clear", command=lambda: textvariable.set("")).grid(
            row=0, column=2, padx=(6, 0)
        )
        self.columnconfigure(0, weight=1)

    def open_calendar(self) -> None:
        CalendarPopup(self, self.textvariable)


class LogPanel(ttk.LabelFrame):
    def __init__(self, parent: tk.Misc, title: str) -> None:
        super().__init__(parent, text=title, padding=10)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.text = tk.Text(self, wrap="word", height=14, state="disabled", bg="#f8fafc")
        self.text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scrollbar.set)

    def write(self, message: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", message + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


def create_openmrs_api(base_url: str, username: str, password: str) -> ApiClient:
    api = ApiClient(base_url=normalize_base_url(base_url), username=username, password=password)
    try:
        api.session_ok = api.login_session()
    except Exception:
        api.session_ok = False
        api.get_json(f"{api.base_url}/session")
    return api


def fetch_openmrs_export_rows(
    api: ApiClient,
    visit_types: Sequence[Dict],
    visit_type_name: str,
    start_date: Optional[str],
    end_date: Optional[str],
    org_unit_code: str,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[List[str], List[Dict[str, str]]]:
    selected_visit = next(
        (visit_type for visit_type in visit_types if visit_type.get("name") == visit_type_name),
        None,
    )
    if not selected_visit:
        raise RuntimeError(f"Visit type '{visit_type_name}' was not found on the current server.")

    if log:
        log(f"Loading visits for '{visit_type_name}'...")
    visits = api.get_visits(start_date, end_date, page_size=100)
    patients = get_patients_by_visit_type(
        visits=visits,
        visit_type_uuid=selected_visit["uuid"],
        visit_start_date=start_date,
        visit_end_date=end_date,
    )
    if not patients:
        raise RuntimeError("No patients matched the selected visit type and date range.")

    if log:
        log(f"Matched {len(patients)} patient(s). Fetching full patient details...")

    program_value = determine_program_from_visit_type(visit_type_name)
    all_obs_columns: List[str] = []
    seen_obs_columns = set()
    buffered_rows: List[Tuple[List[str], Dict[str, str]]] = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(
                build_patient_row,
                api,
                patient_uuid,
                display,
                org_unit_code,
                program_value,
            ): (patient_uuid, display)
            for patient_uuid, display in patients
        }
        for future in as_completed(futures):
            fixed_row, obs_values = future.result()
            for column in obs_values:
                if column not in seen_obs_columns:
                    seen_obs_columns.add(column)
                    all_obs_columns.append(column)
            buffered_rows.append((fixed_row, obs_values))

    headers = [clean_csv_cell(column) for column in DETAIL_COLUMNS + all_obs_columns]
    rows: List[Dict[str, str]] = []
    for fixed_row, obs_values in buffered_rows:
        row_values = [clean_csv_cell(value) for value in fixed_row]
        for column in all_obs_columns:
            row_values.append(clean_csv_cell(obs_values.get(column, "")))
        rows.append(dict(zip(headers, row_values)))

    return headers, rows


def transform_export_records(
    input_headers: Sequence[str],
    input_rows: Sequence[Dict[str, str]],
) -> Tuple[List[Dict[str, str]], Dict[str, int], Dict[str, List[str]]]:
    raise_csv_field_limit()
    required_columns = [column for column in SPECIAL_COLUMNS if column not in input_headers]
    if required_columns:
        raise RuntimeError(
            "The export data is missing required column(s): " + ", ".join(required_columns)
        )

    counts = {MATERNAL_PROGRAM: 0, NEONATAL_PROGRAM: 0, "skipped": 0}
    if not input_rows:
        return [], counts, {}

    first_row = input_rows[0]
    selected_program = normalize_program_value(first_row.get("program", ""))
    if selected_program not in PROGRAM_SPECS:
        raise RuntimeError(
            "The first data row has an unknown program value: "
            f"{first_row.get('program', '')!r}."
        )

    program_fields = load_program_fields([selected_program])
    resolved_fields, missing_fields = resolve_program_sources(
        program_fields,
        input_headers,
        [selected_program],
    )
    ordered_target_headers = deduplicate(
        field.target_header for field in program_fields[selected_program]
    )
    if selected_program == MATERNAL_PROGRAM:
        ordered_target_headers = deduplicate(
            tuple(ordered_target_headers) + MATERNAL_COMPUTED_DIAGNOSIS_HEADERS
        )

    transformed_rows: List[Dict[str, str]] = []
    for row in input_rows:
        program_value = normalize_program_value(row.get("program", ""))
        if program_value not in resolved_fields:
            counts["skipped"] += 1
            continue

        row_org_unit = blank_to_empty(row.get("org_unit", ""))

        transformed_row: "OrderedDict[str, str]" = OrderedDict()
        for column in SPECIAL_COLUMNS:
            transformed_row[column] = blank_to_empty(row.get(column, ""))
        for target_header in ordered_target_headers:
            transformed_row[target_header] = ""

        for target_header in ordered_target_headers:
            field = select_mapping_field(
                resolved_fields[program_value],
                row_org_unit,
                target_header,
            )
            if not field:
                continue
            transformed_row[target_header] = normalize_tracker_value(
                raw_value=row.get(field.source_header, ""),
                data_type=field.data_type,
                options_text=field.options_text,
                target_header=field.target_header,
                program=program_value,
            )

        if program_value == MATERNAL_PROGRAM:
            apply_maternal_diagnosis_transform(transformed_row, row)

        transformed_rows.append(dict(transformed_row))
        counts[program_value] += 1

    return transformed_rows, counts, missing_fields


def import_transformed_records(
    base_url: str,
    username: str,
    password: str,
    rows: Sequence[Dict[str, str]],
    log_path: Optional[Path] = None,
) -> Dict[str, object]:
    configs = build_program_configs()
    client = Dhis2Client(base_url=base_url, username=username, password=password)
    client.validate_credentials()
    import_date = today_date()
    counts = {
        "processed": 0,
        "created_entities": 0,
        "updated_entities": 0,
        "created_enrollments": 0,
        "upserted_events": 0,
        "unsynced_values": 0,
        "row_errors": 0,
        "skipped": 0,
    }
    value_issues = []

    for row in rows:
        program_value = normalize_program_value(row.get("program", ""))
        config = configs.get(program_value)
        if not config:
            counts["skipped"] += 1
            continue

        record_id = extract_row_value(row, "Record ID")
        if not record_id:
            counts["skipped"] += 1
            continue

        try:
            org_unit_id = client.resolve_org_unit(extract_row_value(row, "org_unit"))
            attributes = build_attribute_payload(config, row, issues=value_issues)
            stage_payloads = build_stage_payloads(config, row, import_date, issues=value_issues)
            existing = client.search_tracked_entity(
                record_attribute_id=config.record_id_attribute_id,
                record_id=record_id,
                tracked_entity_type=config.tracked_entity_type,
            )

            if existing:
                tei_id = str(existing.get("trackedEntityInstance") or "").strip()
                client.update_tracked_entity(
                    tei_id,
                    config,
                    org_unit_id,
                    attributes,
                    row=row,
                    issues=value_issues,
                )
                tei = client.get_tracked_entity(tei_id)
                counts["updated_entities"] += 1
            else:
                tei_id = client.create_tracked_entity(
                    config,
                    org_unit_id,
                    attributes,
                    row=row,
                    issues=value_issues,
                )
                tei = client.get_tracked_entity(tei_id)
                counts["created_entities"] += 1

            had_enrollment = any(
                reference_id(enrollment.get("program")) == config.program_uid
                for enrollment in (tei.get("enrollments") or [])
            )
            enrollment = client.ensure_enrollment(tei, config, org_unit_id, import_date)
            if not had_enrollment:
                counts["created_enrollments"] += 1

            for event_payload in stage_payloads:
                event_upserted = client.upsert_event(
                    tei_id=tei_id,
                    enrollment_id=reference_id(enrollment.get("enrollment")),
                    org_unit_id=org_unit_id,
                    event_payload=event_payload,
                    existing_enrollment=enrollment,
                    program_uid=config.program_uid,
                    config=config,
                    row=row,
                    issues=value_issues,
                )
                if event_upserted:
                    counts["upserted_events"] += 1
            counts["processed"] += 1
        except Exception as exc:
            counts["row_errors"] += 1
            add_import_value_issue(
                value_issues,
                row,
                config,
                "Row",
                "Record ID",
                "Record ID",
                config.record_id_attribute_id,
                record_id,
                f"Row could not be fully imported: {format_dhis2_error(exc)}",
            )
            continue

    resolved_log_path = log_path or default_import_log_path()
    write_import_value_log(resolved_log_path, value_issues)
    counts["unsynced_values"] = len(value_issues)
    counts["log_file"] = str(resolved_log_path)
    return counts


class UnifiedApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x780")
        self.root.minsize(980, 680)

        self.api: Optional[ApiClient] = None
        self.visit_types: List[Dict] = []
        self.busy = False

        self.emr_url_var = tk.StringVar()
        self.emr_username_var = tk.StringVar(value="superman")
        self.emr_password_var = tk.StringVar(value="Admin123")
        self.start_date_var = tk.StringVar()
        self.end_date_var = tk.StringVar()
        self.visit_type_var = tk.StringVar()
        self.export_output_var = tk.StringVar(
            value=str(Path(__file__).resolve().with_name("openmrs_export.csv"))
        )

        self.transform_input_var = tk.StringVar()
        self.transform_output_var = tk.StringVar(
            value=str(Path(__file__).resolve().with_name("dhis2_tracker_import.csv"))
        )

        self.dhis2_url_var = tk.StringVar()
        self.dhis2_username_var = tk.StringVar()
        self.dhis2_password_var = tk.StringVar()
        self.import_file_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Choose a menu item to begin.")

        self.buttons: List[ttk.Button] = []
        self.content: Optional[ttk.Frame] = None
        self.log_panel: Optional[LogPanel] = None

        self.configure_style()
        self.build_shell()
        self.show_menu()

    def configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#eef4f8")
        style.configure("Header.TFrame", background="#14324a")
        style.configure("Header.TLabel", background="#14324a", foreground="white")
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Status.TLabel", background="#eef4f8", foreground="#0f5132")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("Form.TLabel", background="#ffffff", foreground="#334155")
        style.configure("Section.TLabel", background="#ffffff", foreground="#0f172a")
        style.configure("Hint.TLabel", background="#ffffff", foreground="#64748b")
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 8))
        style.configure("Primary.TButton", font=("Segoe UI", 11, "bold"), padding=(18, 10))
        style.map("Accent.TButton", background=[("active", "#1d4ed8")])

    def build_shell(self) -> None:
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(22, 18))
        header.pack(fill="x")
        ttk.Label(
            header,
            text=APP_TITLE,
            style="Header.TLabel",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Export, transform, import, or run the full tracker sync in one place.",
            style="Header.TLabel",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        self.content = ttk.Frame(self.root, padding=18)
        self.content.pack(fill="both", expand=True)
        ttk.Label(self.root, textvariable=self.status_var, style="Status.TLabel", padding=(18, 8)).pack(
            fill="x"
        )

    def clear_content(self) -> ttk.Frame:
        if self.content is None:
            raise RuntimeError("Application content frame was not initialized.")
        for child in self.content.winfo_children():
            child.destroy()
        for row in range(8):
            self.content.rowconfigure(row, weight=0, minsize=0, pad=0, uniform="")
        for column in range(4):
            self.content.columnconfigure(column, weight=0, minsize=0, pad=0, uniform="")
        self.buttons = []
        self.log_panel = None
        return self.content

    def show_menu(self) -> None:
        frame = self.clear_content()
        frame.columnconfigure((0, 1), weight=1, uniform="menu")
        frame.rowconfigure((0, 1), weight=1, uniform="menu")
        self.status_var.set("Choose a workflow from the main menu.")

        items = [
            ("EMR Data Export", "Fetch OpenMRS patient data by visit type and date.", "#0f766e", self.show_export),
            ("Transformation", "Convert an OpenMRS export CSV into DHIS2 tracker CSV.", "#2563eb", self.show_transform),
            ("Import to DHIS2", "Import a transformed tracker CSV into DHIS2.", "#7c3aed", self.show_import),
            (
                "EMR-DHIS2 Tracker Sync",
                "Fetch, transform, and import directly without creating an export file.",
                "#dc2626",
                self.show_sync,
            ),
        ]
        for index, (title, subtitle, color, command) in enumerate(items):
            row, column = divmod(index, 2)
            tile = tk.Frame(frame, bg=color, padx=20, pady=18, cursor="hand2")
            tile.grid(row=row, column=column, sticky="nsew", padx=10, pady=10)
            tile.columnconfigure(0, weight=1)
            tk.Label(
                tile,
                text=title,
                bg=color,
                fg="white",
                font=("Segoe UI", 18, "bold"),
                anchor="w",
            ).grid(row=0, column=0, sticky="ew")
            tk.Label(
                tile,
                text=subtitle,
                bg=color,
                fg="#f8fafc",
                font=("Segoe UI", 10),
                anchor="w",
                justify="left",
                wraplength=390,
            ).grid(row=1, column=0, sticky="ew", pady=(10, 24))
            button = tk.Button(
                tile,
                text="Open",
                command=command,
                bg="white",
                fg=color,
                activebackground="#e2e8f0",
                relief="flat",
                padx=20,
                pady=8,
                font=("Segoe UI", 10, "bold"),
                cursor="hand2",
            )
            button.grid(row=2, column=0, sticky="w")
            tile.bind("<Button-1>", lambda _event, action=command: action())

    def section(self, title: str, subtitle: str) -> ttk.Frame:
        frame = self.clear_content()
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        top.columnconfigure(0, weight=1)
        ttk.Label(top, text=title, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(top, text=subtitle, style="Subtitle.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Button(top, text="Main Menu", command=self.show_menu).grid(row=0, column=1, rowspan=2)
        return frame

    def card(self, parent: ttk.Frame, title: str, accent: str) -> ttk.Frame:
        outer = tk.Frame(
            parent,
            bg="#ffffff",
            highlightbackground="#cbd5e1",
            highlightthickness=1,
            bd=0,
        )
        outer.columnconfigure(0, weight=1)
        header = tk.Frame(outer, bg=accent, height=34)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        tk.Label(
            header,
            text=title,
            bg=accent,
            fg="white",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
            padx=12,
        ).pack(fill="both", expand=True)
        body = ttk.Frame(outer, style="Card.TFrame", padding=14)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        outer.body = body  # type: ignore[attr-defined]
        return outer

    def add_field(self, parent: ttk.Frame, row: int, label: str, widget: tk.Widget) -> None:
        ttk.Label(parent, text=label, style="Form.TLabel", width=18).grid(
            row=row, column=0, sticky="w", pady=6, padx=(0, 10)
        )
        widget.grid(row=row, column=1, sticky="ew", pady=5)

    def common_emr_fields(self, parent: ttk.Frame, start_row: int = 0) -> int:
        parent.columnconfigure(1, weight=1)
        self.add_field(parent, start_row, "EMR Server / IP", ttk.Entry(parent, textvariable=self.emr_url_var))
        self.add_field(parent, start_row + 1, "EMR Username", ttk.Entry(parent, textvariable=self.emr_username_var))
        self.add_field(
            parent,
            start_row + 2,
            "EMR Password",
            ttk.Entry(parent, textvariable=self.emr_password_var, show="*"),
        )
        load_button = ttk.Button(parent, text="Connect and Load Visit Types", command=self.load_visit_types)
        load_button.grid(row=start_row + 3, column=1, sticky="w", pady=(4, 10))
        self.buttons.append(load_button)

        self.add_field(parent, start_row + 4, "Start Date", DatePicker(parent, self.start_date_var))
        self.add_field(parent, start_row + 5, "End Date", DatePicker(parent, self.end_date_var))

        combo = ttk.Combobox(parent, textvariable=self.visit_type_var, state="readonly")
        combo["values"] = [item.get("name", "") for item in self.visit_types if item.get("name")]
        self.add_field(parent, start_row + 6, "Visit Type", combo)
        return start_row + 7

    def common_dhis2_fields(self, parent: ttk.Frame, start_row: int = 0) -> int:
        parent.columnconfigure(1, weight=1)
        self.add_field(parent, start_row, "DHIS2 URL", ttk.Entry(parent, textvariable=self.dhis2_url_var))
        self.add_field(parent, start_row + 1, "DHIS2 Username", ttk.Entry(parent, textvariable=self.dhis2_username_var))
        self.add_field(
            parent,
            start_row + 2,
            "DHIS2 Password",
            ttk.Entry(parent, textvariable=self.dhis2_password_var, show="*"),
        )
        return start_row + 3

    def add_log(self, parent: ttk.Frame, row: int, title: str) -> None:
        self.log_panel = LogPanel(parent, title)
        self.log_panel.grid(row=row, column=0, sticky="nsew", pady=(12, 0))
        parent.rowconfigure(row, weight=1)

    def log(self, message: str) -> None:
        if self.log_panel:
            self.root.after(0, lambda: self.log_panel.write(message))

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in self.buttons:
            button.configure(state=state)

    def load_visit_types(self) -> None:
        if self.busy:
            return

        def worker() -> None:
            self.root.after(0, lambda: self.set_busy(True))
            self.root.after(0, lambda: self.status_var.set("Connecting to OpenMRS..."))
            try:
                api = create_openmrs_api(
                    self.emr_url_var.get(),
                    self.emr_username_var.get().strip(),
                    self.emr_password_var.get(),
                )
                visit_types = sorted(
                    api.get_visit_types(),
                    key=lambda item: str(item.get("name", "")).lower(),
                )
                names = [item.get("name", "") for item in visit_types if item.get("name")]
                if not names:
                    raise RuntimeError("No visit types were returned by this OpenMRS server.")

                def done() -> None:
                    self.api = api
                    self.visit_types = visit_types
                    self.visit_type_var.set(names[0])
                    self.status_var.set(f"Connected. Loaded {len(names)} visit type(s).")
                    self.log(f"Connected to {api.base_url}")
                    self.set_busy(False)
                    self.refresh_current_section()

                self.root.after(0, done)
            except Exception as exc:
                self.root.after(0, lambda exc=exc: self.handle_error("Connection failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_current_section(self) -> None:
        current = getattr(self, "_current_section", "")
        if current == "export":
            self.show_export()
        elif current == "sync":
            self.show_sync()

    def validate_emr_inputs(self) -> Tuple[Optional[str], Optional[str], str]:
        start_date = normalize_date_filter(self.start_date_var.get())
        end_date = normalize_date_filter(self.end_date_var.get())
        validate_date_range(start_date, end_date)
        visit_type_name = self.visit_type_var.get().strip()
        if not visit_type_name:
            raise ValueError("Load visit types and choose one visit type.")
        if not self.emr_username_var.get().strip() or not self.emr_password_var.get():
            raise ValueError("Enter EMR username and password.")
        return start_date, end_date, visit_type_name

    def validate_dhis2_inputs(self) -> Tuple[str, str, str]:
        url = self.dhis2_url_var.get().strip()
        username = self.dhis2_username_var.get().strip()
        password = self.dhis2_password_var.get()
        if not url:
            raise ValueError("Enter the DHIS2 URL.")
        if not username or not password:
            raise ValueError("Enter the DHIS2 username and password.")
        return url, username, password

    def show_export(self) -> None:
        self._current_section = "export"
        frame = self.section("EMR Data Export", "Export OpenMRS data to a CSV file.")
        form_card = self.card(frame, "OpenMRS connection and export options", "#0f766e")
        form_card.grid(row=1, column=0, sticky="ew")
        form = form_card.body  # type: ignore[attr-defined]
        next_row = self.common_emr_fields(form)
        output_frame = ttk.Frame(form)
        output_frame.columnconfigure(0, weight=1)
        ttk.Entry(output_frame, textvariable=self.export_output_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_frame, text="Browse", command=self.browse_export_output).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.add_field(form, next_row, "Output CSV", output_frame)
        export_button = ttk.Button(
            form,
            text="Export EMR Data",
            style="Primary.TButton",
            command=self.run_export,
        )
        export_button.grid(row=next_row + 1, column=1, sticky="w", pady=(12, 0))
        self.buttons.append(export_button)
        self.add_log(frame, 2, "Export Log")

    def browse_export_output(self) -> None:
        visit_type = self.visit_type_var.get().strip()
        selected = filedialog.asksaveasfilename(
            title="Choose export file",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=(sanitize_filename(visit_type) + ".csv") if visit_type else "openmrs_export.csv",
        )
        if selected:
            self.export_output_var.set(selected)

    def run_export(self) -> None:
        try:
            start_date, end_date, visit_type_name = self.validate_emr_inputs()
            output_path = Path(self.export_output_var.get().strip())
            if not output_path.name:
                raise ValueError("Choose where to save the CSV export.")
        except Exception as exc:
            messagebox.showerror("Export details required", str(exc))
            return

        def worker() -> None:
            self.root.after(0, lambda: self.set_busy(True))
            self.root.after(0, lambda: self.status_var.set("Exporting EMR data..."))
            try:
                api = self.api or create_openmrs_api(
                    self.emr_url_var.get(),
                    self.emr_username_var.get().strip(),
                    self.emr_password_var.get(),
                )
                visit_types = self.visit_types or api.get_visit_types()
                selected = next((item for item in visit_types if item.get("name") == visit_type_name), None)
                if not selected:
                    raise RuntimeError(f"Visit type '{visit_type_name}' was not found.")
                visits = api.get_visits(start_date, end_date, page_size=100)
                patients = get_patients_by_visit_type(
                    visits,
                    selected["uuid"],
                    start_date,
                    end_date,
                )
                if not patients:
                    raise RuntimeError("No patients matched the selected visit type and date range.")
                count = write_patients_csv(
                    api,
                    patients,
                    output_path,
                    self.emr_username_var.get().strip(),
                    determine_program_from_visit_type(visit_type_name),
                    fetch_concurrency=4,
                )

                def done() -> None:
                    self.api = api
                    self.visit_types = list(visit_types)
                    self.status_var.set(f"Export complete. {count} patient(s) written.")
                    self.log(f"Export finished: {count} patient(s) written to {output_path}")
                    self.set_busy(False)
                    messagebox.showinfo("Export complete", f"Exported {count} patient(s) to:\n{output_path}")

                self.root.after(0, done)
            except Exception as exc:
                self.root.after(0, lambda exc=exc: self.handle_error("Export failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def show_transform(self) -> None:
        self._current_section = "transform"
        frame = self.section("Transformation", "Transform an EMR export CSV into a DHIS2 tracker CSV.")
        form_card = self.card(frame, "CSV transformation", "#2563eb")
        form_card.grid(row=1, column=0, sticky="ew")
        form = form_card.body  # type: ignore[attr-defined]
        input_frame = ttk.Frame(form)
        input_frame.columnconfigure(0, weight=1)
        ttk.Entry(input_frame, textvariable=self.transform_input_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(input_frame, text="Browse", command=self.browse_transform_input).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.add_field(form, 0, "OpenMRS Export CSV", input_frame)
        output_frame = ttk.Frame(form)
        output_frame.columnconfigure(0, weight=1)
        ttk.Entry(output_frame, textvariable=self.transform_output_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_frame, text="Browse", command=self.browse_transform_output).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.add_field(form, 1, "Output CSV", output_frame)
        button = ttk.Button(
            form,
            text="Transform CSV",
            style="Primary.TButton",
            command=self.run_transform,
        )
        button.grid(row=2, column=1, sticky="w", pady=(12, 0))
        self.buttons.append(button)
        self.add_log(frame, 2, "Transformation Log")

    def browse_transform_input(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose OpenMRS export CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.transform_input_var.set(selected)

    def browse_transform_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="Choose transformed CSV file",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="dhis2_tracker_import.csv",
        )
        if selected:
            self.transform_output_var.set(selected)

    def run_transform(self) -> None:
        input_path = Path(self.transform_input_var.get().strip())
        output_path = Path(self.transform_output_var.get().strip())
        if not input_path.is_file():
            messagebox.showerror("Input file required", "Choose a valid OpenMRS export CSV file.")
            return
        if not output_path.name:
            messagebox.showerror("Output file required", "Choose where to save the transformed CSV.")
            return

        def worker() -> None:
            self.root.after(0, lambda: self.set_busy(True))
            self.root.after(0, lambda: self.status_var.set("Transforming CSV..."))
            try:
                row_count, counts, missing = transform_rows(input_path, output_path)

                def done() -> None:
                    self.status_var.set(f"Transformation complete. {row_count} row(s) written.")
                    self.log(f"Input file: {input_path}")
                    self.log(f"Output file: {output_path}")
                    self.log(f"Maternal rows transformed: {counts[MATERNAL_PROGRAM]}")
                    self.log(f"Neonatal rows transformed: {counts[NEONATAL_PROGRAM]}")
                    if counts["skipped"]:
                        self.log(f"Rows skipped: {counts['skipped']}")
                    for program, fields in missing.items():
                        if fields:
                            self.log(f"{program}: {len(fields)} mapped field(s) could not be matched.")
                    self.set_busy(False)
                    messagebox.showinfo("Transformation complete", f"Transformed {row_count} row(s) into:\n{output_path}")

                self.root.after(0, done)
            except Exception as exc:
                self.root.after(0, lambda exc=exc: self.handle_error("Transformation failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def show_import(self) -> None:
        self._current_section = "import"
        frame = self.section("Import to DHIS2", "Import a transformed tracker CSV into DHIS2.")
        form_card = self.card(frame, "DHIS2 connection and import file", "#7c3aed")
        form_card.grid(row=1, column=0, sticky="ew")
        form = form_card.body  # type: ignore[attr-defined]
        next_row = self.common_dhis2_fields(form)
        file_frame = ttk.Frame(form)
        file_frame.columnconfigure(0, weight=1)
        ttk.Entry(file_frame, textvariable=self.import_file_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(file_frame, text="Browse", command=self.browse_import_file).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.add_field(form, next_row, "Transformed CSV", file_frame)
        button = ttk.Button(
            form,
            text="Import to DHIS2",
            style="Primary.TButton",
            command=self.run_import,
        )
        button.grid(row=next_row + 1, column=1, sticky="w", pady=(12, 0))
        self.buttons.append(button)
        self.add_log(frame, 2, "Import Log")

    def browse_import_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose transformed CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.import_file_var.set(selected)

    def run_import(self) -> None:
        try:
            url, username, password = self.validate_dhis2_inputs()
            input_path = Path(self.import_file_var.get().strip())
            if not input_path.is_file():
                raise ValueError("Choose a valid transformed CSV file.")
        except Exception as exc:
            messagebox.showerror("Import details required", str(exc))
            return

        def worker() -> None:
            self.root.after(0, lambda: self.set_busy(True))
            self.root.after(0, lambda: self.status_var.set("Importing tracker data into DHIS2..."))
            try:
                counts = import_rows(url, username, password, input_path)
                self.root.after(0, lambda: self.import_done(counts, "Import complete"))
            except Exception as exc:
                self.root.after(0, lambda exc=exc: self.handle_error("Import failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def import_done(self, counts: Dict[str, object], title: str) -> None:
        self.status_var.set(f"{title}. {counts['processed']} row(s) processed.")
        self.log(f"Rows processed: {counts['processed']}")
        self.log(f"Tracked entities created: {counts['created_entities']}")
        self.log(f"Tracked entities updated: {counts['updated_entities']}")
        self.log(f"Enrollments created: {counts['created_enrollments']}")
        self.log(f"Events created or updated: {counts['upserted_events']}")
        self.log(f"Values discarded: {counts['unsynced_values']}")
        if counts["row_errors"]:
            self.log(f"Rows with import errors: {counts['row_errors']}")
        self.log(f"Import value log: {counts['log_file']}")
        if counts["skipped"]:
            self.log(f"Rows skipped: {counts['skipped']}")
        self.set_busy(False)
        messagebox.showinfo(title, f"Processed {counts['processed']} row(s).")

    def show_sync(self) -> None:
        self._current_section = "sync"
        frame = self.section(
            "EMR-DHIS2 Tracker Sync",
            "Fetch from EMR, transform in memory, and import directly to DHIS2.",
        )
        frame.rowconfigure(2, weight=0)
        frame.rowconfigure(3, weight=1)

        cards = ttk.Frame(frame)
        cards.grid(row=1, column=0, sticky="ew")
        cards.columnconfigure((0, 1), weight=1, uniform="sync")

        emr_card = self.card(cards, "OpenMRS source", "#0f766e")
        emr_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        emr_form = emr_card.body  # type: ignore[attr-defined]
        self.common_emr_fields(emr_form)

        dhis2_card = self.card(cards, "DHIS2 destination", "#7c3aed")
        dhis2_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        dhis2_form = dhis2_card.body  # type: ignore[attr-defined]
        self.common_dhis2_fields(dhis2_form)

        action_bar = tk.Frame(
            frame,
            bg="#fff7ed",
            highlightbackground="#fed7aa",
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        action_bar.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        action_bar.columnconfigure(0, weight=1)
        tk.Label(
            action_bar,
            text="Ready to run the complete EMR to DHIS2 tracker workflow.",
            bg="#fff7ed",
            fg="#7c2d12",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        button = ttk.Button(
            action_bar,
            text="Sync EMR Data to DHIS2 Tracker",
            style="Primary.TButton",
            command=self.run_sync,
        )
        button.grid(row=0, column=1, sticky="e", padx=(14, 0))
        self.buttons.append(button)
        self.add_log(frame, 3, "Sync Log")

    def run_sync(self) -> None:
        try:
            start_date, end_date, visit_type_name = self.validate_emr_inputs()
            dhis2_url, dhis2_username, dhis2_password = self.validate_dhis2_inputs()
        except Exception as exc:
            messagebox.showerror("Sync details required", str(exc))
            return

        def worker() -> None:
            self.root.after(0, lambda: self.set_busy(True))
            self.root.after(0, lambda: self.status_var.set("Syncing EMR data to DHIS2 tracker..."))
            try:
                api = self.api or create_openmrs_api(
                    self.emr_url_var.get(),
                    self.emr_username_var.get().strip(),
                    self.emr_password_var.get(),
                )
                visit_types = self.visit_types or sorted(
                    api.get_visit_types(),
                    key=lambda item: str(item.get("name", "")).lower(),
                )
                headers, export_rows = fetch_openmrs_export_rows(
                    api=api,
                    visit_types=visit_types,
                    visit_type_name=visit_type_name,
                    start_date=start_date,
                    end_date=end_date,
                    org_unit_code=self.emr_username_var.get().strip(),
                    log=self.log,
                )
                self.log(f"Fetched {len(export_rows)} EMR row(s). Transforming in memory...")
                transformed_rows, transform_counts, missing = transform_export_records(headers, export_rows)
                self.log(f"Transformed {len(transformed_rows)} row(s). Importing to DHIS2...")
                for program, fields in missing.items():
                    if fields:
                        self.log(f"{program}: {len(fields)} mapped field(s) could not be matched.")
                counts = import_transformed_records(
                    dhis2_url,
                    dhis2_username,
                    dhis2_password,
                    transformed_rows,
                )

                def done() -> None:
                    self.api = api
                    self.visit_types = list(visit_types)
                    self.log(f"Maternal rows transformed: {transform_counts[MATERNAL_PROGRAM]}")
                    self.log(f"Neonatal rows transformed: {transform_counts[NEONATAL_PROGRAM]}")
                    self.import_done(counts, "Sync complete")

                self.root.after(0, done)
            except Exception as exc:
                self.root.after(0, lambda exc=exc: self.handle_error("Sync failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def handle_error(self, title: str, exc: Exception) -> None:
        self.status_var.set(f"{title}: {exc}")
        self.log(f"{title}: {exc}")
        self.set_busy(False)
        messagebox.showerror(title, str(exc))


def main() -> None:
    root = tk.Tk()
    UnifiedApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
