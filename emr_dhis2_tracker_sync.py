from __future__ import annotations

import calendar
import csv
import tempfile
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
    infer_enrollment_date,
    reference_id,
    today_date,
    write_import_value_log,
)
from transform_export_to_dhis2_csv import (
    CONTEXT_COLUMNS,
    MATERNAL_PROGRAM,
    NEONATAL_PROGRAM,
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
    set_mapping_files,
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


class SyncPage(ttk.Frame):
    def __init__(self, parent, on_back_to_menu):
        super().__init__(parent)
        self.parent = parent
        self.on_back_to_menu = on_back_to_menu

        self.api: Optional[ApiClient] = None
        self.visit_types: List[Dict] = []
        self.busy = False

        self.emr_url_var = tk.StringVar()
        self.emr_username_var = tk.StringVar(value="superman")
        self.emr_password_var = tk.StringVar(value="Admin123")
        self.start_date_var = tk.StringVar()
        self.end_date_var = tk.StringVar()
        self.visit_type_var = tk.StringVar()

        self.mapping_var = tk.StringVar()
        self.dict_var = tk.StringVar()
        self.value_mapping_var = tk.StringVar()

        self.dhis2_url_var = tk.StringVar()
        self.dhis2_username_var = tk.StringVar()
        self.dhis2_password_var = tk.StringVar()

        self.status_var = tk.StringVar(value="Select mapping files, then configure EMR and DHIS2 settings.")

        self.buttons: List[ttk.Button] = []
        self.log_panel: Optional[LogPanel] = None

        self.configure_style()
        self.build_ui()

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

    def build_ui(self) -> None:
        # Header with Back button
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 18))
        header.pack(fill="x")
        
        back_btn = ttk.Button(header, text="← Back to Main Menu", command=self.go_back)
        back_btn.pack(side="left")
        
        ttk.Label(
            header,
            text="EMR-DHIS2 Tracker Direct Sync",
            style="Header.TLabel",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="center")
        
        ttk.Label(
            header,
            text="Export from EMR, transform using mapping files, and import directly to DHIS2 Tracker.",
            style="Header.TLabel",
            font=("Segoe UI", 10),
        ).pack(anchor="center", pady=(4, 0))

        # Main content frame
        content = ttk.Frame(self, padding=18)
        content.pack(fill="both", expand=True)

        # Mapping files card
        mapping_card = self.card(content, "Mapping Files (Required)", "#f59e0b")
        mapping_card.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        mapping_form = mapping_card.body
        mapping_form.columnconfigure(2, weight=0)
        
        # Mapping Excel file
        self.add_field(mapping_form, 0, "Mapping Excel File", ttk.Entry(mapping_form, textvariable=self.mapping_var))
        browse_mapping_btn = ttk.Button(mapping_form, text="Browse", command=self.browse_mapping)
        browse_mapping_btn.grid(row=0, column=2, padx=(8, 0))
        self.buttons.append(browse_mapping_btn)
        
        # Dictionary Excel file
        self.add_field(mapping_form, 1, "Dictionary Excel File", ttk.Entry(mapping_form, textvariable=self.dict_var))
        browse_dict_btn = ttk.Button(mapping_form, text="Browse", command=self.browse_dictionary)
        browse_dict_btn.grid(row=1, column=2, padx=(8, 0))
        self.buttons.append(browse_dict_btn)
        
        # Value Mapping CSV (optional)
        self.add_field(mapping_form, 2, "Value Mapping CSV (Optional)", ttk.Entry(mapping_form, textvariable=self.value_mapping_var))
        browse_value_btn = ttk.Button(mapping_form, text="Browse", command=self.browse_value_mapping)
        browse_value_btn.grid(row=2, column=2, padx=(8, 0))
        self.buttons.append(browse_value_btn)

        # EMR and DHIS2 cards side by side
        cards = ttk.Frame(content)
        cards.grid(row=1, column=0, sticky="ew", pady=(15, 15))
        cards.columnconfigure((0, 1), weight=1, uniform="sync")

        emr_card = self.card(cards, "OpenMRS source", "#0f766e")
        emr_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        emr_form = emr_card.body
        self.common_emr_fields(emr_form)

        dhis2_card = self.card(cards, "DHIS2 destination", "#7c3aed")
        dhis2_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        dhis2_form = dhis2_card.body
        self.common_dhis2_fields(dhis2_form)

        # Action bar
        action_bar = tk.Frame(
            content,
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
        
        # Log panel
        self.add_log(content, 3, "Sync Log")
        
        # Configure grid weights
        content.columnconfigure(0, weight=1)
        content.rowconfigure(3, weight=1)

    def go_back(self):
        self.on_back_to_menu()
        self.destroy()

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
        outer.body = body
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
            self.after(0, lambda: self.log_panel.write(message))

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in self.buttons:
            button.configure(state=state)

    def load_visit_types(self) -> None:
        if self.busy:
            return

        def worker() -> None:
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Connecting to OpenMRS..."))
            try:
                api = self.create_openmrs_api(
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
                    # Rebuild UI to update visit type combo
                    self.build_ui()

                self.after(0, done)
            except Exception as exc:
                self.after(0, lambda exc=exc: self.handle_error("Connection failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def create_openmrs_api(self, base_url: str, username: str, password: str) -> ApiClient:
        api = ApiClient(base_url=normalize_base_url(base_url), username=username, password=password)
        try:
            api.session_ok = api.login_session()
        except Exception:
            api.session_ok = False
            api.get_json(f"{api.base_url}/session")
        return api

    def browse_mapping(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Mapping Excel File (contains both Maternal and Neonatal mappings)",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        )
        if selected:
            self.mapping_var.set(selected)

    def browse_dictionary(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Dictionary Excel File",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        )
        if selected:
            self.dict_var.set(selected)

    def browse_value_mapping(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Value Mapping CSV File (optional)",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.value_mapping_var.set(selected)

    def run_sync(self) -> None:
        try:
            start_date = normalize_date_filter(self.start_date_var.get())
            end_date = normalize_date_filter(self.end_date_var.get())
            validate_date_range(start_date, end_date)
            visit_type_name = self.visit_type_var.get().strip()
            if not visit_type_name:
                raise ValueError("Load visit types and choose one visit type.")
            if not self.emr_username_var.get().strip() or not self.emr_password_var.get():
                raise ValueError("Enter EMR username and password.")
            
            dhis2_url = self.dhis2_url_var.get().strip()
            dhis2_username = self.dhis2_username_var.get().strip()
            dhis2_password = self.dhis2_password_var.get()
            if not dhis2_url:
                raise ValueError("Enter the DHIS2 URL.")
            if not dhis2_username or not dhis2_password:
                raise ValueError("Enter the DHIS2 username and password.")
            
            mapping_path = Path(self.mapping_var.get().strip())
            dict_path = Path(self.dict_var.get().strip())
            value_mapping_path = Path(self.value_mapping_var.get().strip()) if self.value_mapping_var.get().strip() else None
            
            if not mapping_path.is_file():
                raise ValueError("Please select a valid mapping Excel file.")
            if not dict_path.is_file():
                raise ValueError("Please select a valid dictionary Excel file.")
            
            # Set the mapping files globally
            set_mapping_files(mapping_path, dict_path, value_mapping_path)
            
            if value_mapping_path and value_mapping_path.exists():
                self.log(f"Loaded value mappings from: {value_mapping_path}")
            elif value_mapping_path:
                self.log(f"Warning: Value mapping file not found at {value_mapping_path}")
            else:
                self.log("No value mapping file provided (optional)")
                
        except Exception as exc:
            messagebox.showerror("Sync details required", str(exc))
            return

        def worker() -> None:
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Syncing EMR data to DHIS2 tracker..."))
            try:
                api = self.create_openmrs_api(
                    self.emr_url_var.get(),
                    self.emr_username_var.get().strip(),
                    self.emr_password_var.get(),
                )
                visit_types = self.visit_types or sorted(
                    api.get_visit_types(),
                    key=lambda item: str(item.get("name", "")).lower(),
                )
                headers, export_rows = self.fetch_openmrs_export_rows(
                    api=api,
                    visit_types=visit_types,
                    visit_type_name=visit_type_name,
                    start_date=start_date,
                    end_date=end_date,
                    org_unit_code=self.emr_username_var.get().strip(),
                )
                self.log(f"Fetched {len(export_rows)} EMR row(s). Transforming in memory...")
                with tempfile.TemporaryDirectory(prefix="emr_dhis2_sync_") as temp_dir:
                    temp_path = Path(temp_dir)
                    export_path = temp_path / "openmrs_export.csv"
                    transformed_path = temp_path / "dhis2_tracker_import.csv"
                    self.write_temporary_export_csv(export_path, headers, export_rows)
                    row_count, transform_counts, missing = transform_rows(
                        export_path,
                        transformed_path,
                    )
                    self.log(f"Transformed {row_count} row(s). Importing to DHIS2...")
                    for program, fields in missing.items():
                        if fields:
                            self.log(f"{program}: {len(fields)} mapped field(s) could not be matched.")
                    counts = import_rows(
                        dhis2_url,
                        dhis2_username,
                        dhis2_password,
                        transformed_path,
                    )

                def done() -> None:
                    self.api = api
                    self.visit_types = list(visit_types)
                    self.log(f"Maternal rows transformed: {transform_counts[MATERNAL_PROGRAM]}")
                    self.log(f"Neonatal rows transformed: {transform_counts[NEONATAL_PROGRAM]}")
                    self.import_done(counts, "Sync complete")

                self.after(0, done)
            except Exception as exc:
                self.after(0, lambda exc=exc: self.handle_error("Sync failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def fetch_openmrs_export_rows(
        self,
        api: ApiClient,
        visit_types: Sequence[Dict],
        visit_type_name: str,
        start_date: Optional[str],
        end_date: Optional[str],
        org_unit_code: str,
    ) -> Tuple[List[str], List[Dict[str, str]]]:
        selected_visit = next(
            (visit_type for visit_type in visit_types if visit_type.get("name") == visit_type_name),
            None,
        )
        if not selected_visit:
            raise RuntimeError(f"Visit type '{visit_type_name}' was not found on the current server.")

        self.log(f"Loading visits for '{visit_type_name}'...")
        visits = api.get_visits(start_date, end_date, page_size=100)
        patients = get_patients_by_visit_type(
            visits=visits,
            visit_type_uuid=selected_visit["uuid"],
            visit_start_date=start_date,
            visit_end_date=end_date,
        )
        if not patients:
            raise RuntimeError("No patients matched the selected visit type and date range.")

        self.log(f"Matched {len(patients)} patient(s). Fetching full patient details...")

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
                    visit_date,
                ): (patient_uuid, display)
                for patient_uuid, display, visit_date in patients
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

    def write_temporary_export_csv(
        self,
        path: Path,
        headers: Sequence[str],
        rows: Sequence[Dict[str, str]],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(headers), quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(rows)

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

    def handle_error(self, title: str, exc: Exception) -> None:
        self.status_var.set(f"{title}: {exc}")
        self.log(f"{title}: {exc}")
        self.set_busy(False)
        messagebox.showerror(title, str(exc))


def main() -> None:
    root = tk.Tk()
    app = SyncPage(root, lambda: None)
    root.mainloop()


if __name__ == "__main__":
    main()