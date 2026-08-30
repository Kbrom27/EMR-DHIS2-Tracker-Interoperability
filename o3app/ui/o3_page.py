from __future__ import annotations

import csv
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from o3app.clients.openmrs_client import ApiClient, normalize_base_url
from o3app.config import (
    FACILITIES,
    FACILITY_CODES,
    MATERNAL_PROGRAM,
    NEONATAL_PROGRAM,
    O3_METADATA_PATH,
    O3_SCHEMA_ROOT,
    RESOURCES_DIR,
)
from o3app.import_.importer import import_rows
from o3app.export.extractors import sanitize_filename
from o3app.extract import (
    determine_program_from_visit_type,
    get_patients_by_visit_type,
    normalize_date_filter,
    validate_date_range,
    write_o3_patients_csv,
)
from o3app.mappings import DEFAULT_MATERNAL_DICTIONARY, DEFAULT_NEONATAL_DICTIONARY
from o3app.schemas import FormRegistry, load_default_forms
from o3app.transform.mapping import set_mapping_files
from o3app.transform.pipeline import transform_rows
from o3app.ui.components import DatePicker, LogPanel
from o3app.utils import read_xlsx_rows

O3_MAPPING_DIR = RESOURCES_DIR / "O3"

NAV_ITEMS = [
    ("export", "Export O3 Patients"),
    ("transform", "Transform O3 Export"),
    ("import", "Import to DHIS2"),
    ("sync", "Full O3 Sync"),
]


class O3Page(ttk.Frame):
    def __init__(self, parent, on_back_to_menu):
        super().__init__(parent)
        self.parent = parent
        self.on_back_to_menu = on_back_to_menu

        self.api: Optional[ApiClient] = None
        self.visit_types: List[Dict] = []
        self.busy = False

        self.base_url_var = tk.StringVar()
        self.username_var = tk.StringVar(value="superman")
        self.password_var = tk.StringVar(value="Admin123")
        self.facility_var = tk.StringVar(value=FACILITIES[0][0])
        self.start_date_var = tk.StringVar()
        self.end_date_var = tk.StringVar()
        self.visit_type_var = tk.StringVar()
        self.export_file_var = tk.StringVar(
            value=str(Path.cwd() / "O3 Export" / "openmrs3_export.csv")
        )
        self.output_dir_var = tk.StringVar(
            value=str(Path.cwd() / "O3 Export")
        )
        self.input_csv_var = tk.StringVar(
            value=str(Path.cwd() / "O3 Export" / "openmrs3_export.csv")
        )
        self.transform_output_var = tk.StringVar(
            value=str(Path.cwd() / "O3 Export" / "dhis2_tracker_import.csv")
        )
        self.import_file_var = tk.StringVar(
            value=str(Path.cwd() / "O3 Export" / "dhis2_tracker_import.csv")
        )

        self.dhis2_url_var = tk.StringVar(value="https://imnid.mohdigitalhealth.gov.et")
        self.dhis2_username_var = tk.StringVar()
        self.dhis2_password_var = tk.StringVar()

        self.status_var = tk.StringVar(
            value="Use the menu above to export, transform, import, or run a full O3 sync."
        )

        self.buttons: List[tk.Widget] = []
        self._visit_type_combos: List[ttk.Combobox] = []
        self._views: Dict[str, ttk.Frame] = {}
        self._nav_buttons: Dict[str, tk.Button] = {}

        self._build_ui()
        self._show_view("export")

    def _build_ui(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 14))
        header.pack(fill="x")
        back_btn = ttk.Button(header, text="\u2190 Back to Main Menu", command=self.go_back)
        back_btn.pack(side="left")
        ttk.Label(
            header,
            text="OpenMRS 3 (O3) Workflow",
            style="Header.TLabel",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="center")
        ttk.Label(
            header,
            text="Export O3 data, transform it, import to DHIS2, or run the full sync.",
            style="Header.TLabel",
            font=("Segoe UI", 10),
        ).pack(anchor="center", pady=(4, 0))

        nav_bar = tk.Frame(self, bg="#eef4f8", padx=4, pady=6)
        nav_bar.pack(fill="x")
        for key, label in NAV_ITEMS:
            btn = self._make_nav_button(nav_bar, label, lambda k=key: self._show_view(k))
            btn.pack(side="left", padx=4)
            self._nav_buttons[key] = btn

        self._build_scroll_area()

        self._views["export"] = self._build_export_view(self.scroll_inner)
        self._views["transform"] = self._build_transform_view(self.scroll_inner)
        self._views["import"] = self._build_import_view(self.scroll_inner)
        self._views["sync"] = self._build_sync_view(self.scroll_inner)

        ttk.Label(self, textvariable=self.status_var, foreground="#1f4e79").pack(
            fill="x", padx=16, pady=(6, 0)
        )

        self.log_panel = LogPanel(self, "OpenMRS 3 Workflow Log")
        self.log_panel.pack(fill="both", expand=True, padx=16, pady=(6, 14))

    def _build_scroll_area(self) -> None:
        self.scroll_canvas = tk.Canvas(self, bg="#eef4f8", highlightthickness=0)
        self.scroll_canvas.pack(fill="both", expand=True)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.scroll_canvas.yview)
        self.scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scroll_inner = ttk.Frame(self.scroll_canvas)
        self.scroll_window_id = self.scroll_canvas.create_window(
            (0, 0), window=self.scroll_inner, anchor="nw"
        )
        self.scroll_inner.bind(
            "<Configure>",
            lambda event: self.scroll_canvas.configure(
                scrollregion=self.scroll_canvas.bbox("all")
            ),
        )
        self.scroll_canvas.bind(
            "<Configure>",
            lambda event: self.scroll_canvas.itemconfigure(
                self.scroll_window_id, width=event.width
            ),
        )
        self.scroll_inner.bind("<Enter>", self._bind_mousewheel)
        self.scroll_inner.bind("<Leave>", self._unbind_mousewheel)
        self.scroll_canvas.bind("<Enter>", self._bind_mousewheel)
        self.scroll_canvas.bind("<Leave>", self._unbind_mousewheel)

    def _bind_mousewheel(self, _event) -> None:
        self.scroll_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event) -> None:
        self.scroll_canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event) -> None:
        if event.delta:
            self.scroll_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _make_nav_button(self, parent, label: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=label,
            bd=0,
            relief="flat",
            padx=18,
            pady=6,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
            command=command,
        )

    def _show_view(self, name: str) -> None:
        for existing in self._views.values():
            existing.pack_forget()
        self._views[name].pack(fill="both", expand=True, padx=16, pady=(6, 0))
        for key, btn in self._nav_buttons.items():
            active = key == name
            btn.configure(
                bg="#0d9488" if active else "#eef4f8",
                fg="white" if active else "#0f172a",
                activebackground="#0d9488" if active else "#e2e8f0",
            )

    def _field(self, parent: ttk.Frame, row: int, label: str, widget: tk.Widget) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, width=22).grid(
            row=row, column=0, sticky="w", pady=5, padx=(0, 10)
        )
        widget.grid(row=row, column=1, sticky="ew", pady=5)

    def _facility_field(self, parent: ttk.Frame, row: int) -> None:
        combo = ttk.Combobox(parent, textvariable=self.facility_var, state="readonly", width=44)
        combo["values"] = [name for name, _code in FACILITIES]
        self._field(parent, row, "Facility", combo)

    def _selected_facility_code(self) -> str:
        facility_name = self.facility_var.get().strip()
        return FACILITY_CODES.get(facility_name, facility_name)

    def _browse_field(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, command) -> None:
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        ttk.Entry(frame, textvariable=var).grid(row=0, column=0, sticky="ew")
        btn = ttk.Button(frame, text="Browse", command=command)
        btn.grid(row=0, column=1, padx=(8, 0))
        self.buttons.append(btn)
        self._field(parent, row, label, frame)

    def _build_export_view(self, parent) -> ttk.Frame:
        view = ttk.Frame(parent, padding=16)
        self._field(view, 0, "EMR Server / IP", ttk.Entry(view, textvariable=self.base_url_var, width=60))
        self._field(view, 1, "EMR Username", ttk.Entry(view, textvariable=self.username_var, width=30))
        self._field(view, 2, "EMR Password", ttk.Entry(view, textvariable=self.password_var, show="*", width=30))

        self._facility_field(view, 3)

        row = 4
        connect_frame = ttk.Frame(view)
        connect_frame.columnconfigure(1, weight=1)
        ttk.Button(connect_frame, text="Connect and Load Visit Types", command=self.connect_and_load).grid(
            row=0, column=1, sticky="w", pady=(2, 6)
        )
        self._field(view, row, " ", connect_frame)
        self.buttons.append(connect_frame.winfo_children()[0])

        row += 1
        date_frame = ttk.Frame(view)
        DatePicker(date_frame, self.start_date_var).pack(side="left", padx=(0, 18))
        ttk.Label(date_frame, text="End Date").pack(side="left")
        DatePicker(date_frame, self.end_date_var).pack(side="left", padx=(8, 0))
        self._field(view, row, "Start Date", date_frame)

        row += 1
        combo = ttk.Combobox(view, textvariable=self.visit_type_var, state="readonly", width=44)
        self._visit_type_combos.append(combo)
        self._field(view, row, "Visit Type", combo)

        row += 1
        self._browse_field(view, row, "Export Output File", self.export_file_var, self.browse_export_output)

        row += 1
        ttk.Label(
            view,
            text=f"Form schemas loaded from:\n{O3_SCHEMA_ROOT}\nMetadata: {O3_METADATA_PATH}",
            foreground="#64748b", justify="left", anchor="w",
        ).grid(row=row, column=1, sticky="w", pady=(2, 8))

        row += 1
        self._field(view, row, " ",
                    tk.Label(view, text="Form schemas are used to write column labels instead of backend concept names.",
                             foreground="#64748b"))
        row += 1
        export_btn = ttk.Button(view, text="Export O3 Patients", command=self.export_patients)
        export_btn.grid(row=row, column=1, sticky="w", pady=(10, 4))
        self.buttons.append(export_btn)

        spacer = ttk.Frame(view)
        spacer.grid(row=row + 1, column=0, columnspan=2, sticky="nsew")
        view.rowconfigure(row + 1, weight=1)
        return view

    def _build_transform_view(self, parent) -> ttk.Frame:
        view = ttk.Frame(parent, padding=16)
        self._browse_field(view, 0, "O3 Export CSV", self.input_csv_var, self.browse_input_csv)
        hint = tk.Label(
            view,
            text="Choose any previously exported O3 CSV (e.g. openmrs3_export.csv), not only the last export.",
            foreground="#64748b", justify="left", anchor="w",
        )
        hint.grid(row=1, column=1, sticky="w")
        self._browse_field(view, 2, "Transformed Output File", self.transform_output_var, self.browse_transform_output)

        row = 3
        mapping_frame = ttk.Frame(view)
        mapping_frame.columnconfigure(0, weight=1)
        ttk.Button(mapping_frame, text="Verify Mapping Files", command=self.load_mapping_files).grid(
            row=0, column=0, sticky="w"
        )
        self._field(view, row, " ", mapping_frame)
        self.buttons.append(mapping_frame.winfo_children()[0])

        row += 1
        ttk.Label(
            view,
            text=f"Mapping files are loaded from:\n{O3_MAPPING_DIR}",
            foreground="#64748b", justify="left", anchor="w",
        ).grid(row=row, column=1, sticky="w", pady=(2, 8))

        row += 1
        transform_btn = ttk.Button(view, text="Transform O3 Export", command=self.transform_export)
        transform_btn.grid(row=row, column=1, sticky="w", pady=(10, 4))
        self.buttons.append(transform_btn)

        spacer = ttk.Frame(view)
        spacer.grid(row=row + 1, column=0, columnspan=2, sticky="nsew")
        view.rowconfigure(row + 1, weight=1)
        return view

    def _build_import_view(self, parent) -> ttk.Frame:
        view = ttk.Frame(parent, padding=16)
        self._field(view, 0, "DHIS2 URL", ttk.Entry(view, textvariable=self.dhis2_url_var, width=60))
        self._field(view, 1, "DHIS2 Username", ttk.Entry(view, textvariable=self.dhis2_username_var, width=30))
        self._field(view, 2, "DHIS2 Password", ttk.Entry(view, textvariable=self.dhis2_password_var, show="*", width=30))
        self._browse_field(view, 3, "Transformed CSV", self.import_file_var, self.browse_import_file)
        hint = tk.Label(
            view,
            text="Choose any previously transformed DHIS2 tracker CSV (e.g. dhis2_tracker_import.csv).",
            foreground="#64748b", justify="left", anchor="w",
        )
        hint.grid(row=4, column=1, sticky="w")

        row = 5
        import_btn = ttk.Button(view, text="Import to DHIS2", command=self.import_transformed)
        import_btn.grid(row=row, column=1, sticky="w", pady=(10, 4))
        self.buttons.append(import_btn)

        spacer = ttk.Frame(view)
        spacer.grid(row=row + 1, column=0, columnspan=2, sticky="nsew")
        view.rowconfigure(row + 1, weight=1)
        return view

    def _build_sync_view(self, parent) -> ttk.Frame:
        view = ttk.Frame(parent, padding=16)
        view.columnconfigure(0, weight=1)

        cards = ttk.Frame(view)
        cards.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        cards.columnconfigure(0, weight=1, uniform="sync_side")
        cards.columnconfigure(1, weight=1, uniform="sync_side")

        # OpenMRS 3 Source Frame
        emr_card = ttk.LabelFrame(cards, text=" OpenMRS 3 Source ", padding=12)
        emr_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self._field(emr_card, 0, "EMR Server / IP", ttk.Entry(emr_card, textvariable=self.base_url_var, width=32))
        self._field(emr_card, 1, "EMR Username", ttk.Entry(emr_card, textvariable=self.username_var, width=24))
        self._field(emr_card, 2, "EMR Password", ttk.Entry(emr_card, textvariable=self.password_var, show="*", width=24))
        self._facility_field(emr_card, 3)

        connect_frame = ttk.Frame(emr_card)
        connect_frame.columnconfigure(1, weight=1)
        ttk.Button(connect_frame, text="Connect & Load Visit Types", command=self.connect_and_load).grid(
            row=0, column=1, sticky="w", pady=(2, 6)
        )
        self._field(emr_card, 4, " ", connect_frame)
        self.buttons.append(connect_frame.winfo_children()[0])

        date_frame = ttk.Frame(emr_card)
        DatePicker(date_frame, self.start_date_var).pack(side="left", padx=(0, 8))
        ttk.Label(date_frame, text="End Date").pack(side="left", padx=(4, 4))
        DatePicker(date_frame, self.end_date_var).pack(side="left")
        self._field(emr_card, 5, "Start Date", date_frame)

        combo = ttk.Combobox(emr_card, textvariable=self.visit_type_var, state="readonly", width=32)
        self._visit_type_combos.append(combo)
        self._field(emr_card, 6, "Visit Type", combo)

        # DHIS2 Destination Frame
        dhis2_card = ttk.LabelFrame(cards, text=" DHIS2 Tracker Destination ", padding=12)
        dhis2_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self._field(dhis2_card, 0, "DHIS2 URL", ttk.Entry(dhis2_card, textvariable=self.dhis2_url_var, width=32))
        self._field(dhis2_card, 1, "DHIS2 Username", ttk.Entry(dhis2_card, textvariable=self.dhis2_username_var, width=24))
        self._field(dhis2_card, 2, "DHIS2 Password", ttk.Entry(dhis2_card, textvariable=self.dhis2_password_var, show="*", width=24))
        self._browse_field(dhis2_card, 3, "Export Output File", self.export_file_var, self.browse_export_output)
        self._browse_field(dhis2_card, 4, "Transformed Output File", self.transform_output_var, self.browse_transform_output)

        # Action bar & Metadata info
        action_frame = ttk.Frame(view)
        action_frame.grid(row=1, column=0, sticky="ew", pady=(5, 0))

        ttk.Label(
            action_frame,
            text=f"Form schemas loaded from:\n{O3_SCHEMA_ROOT}\nMetadata: {O3_METADATA_PATH}",
            foreground="#64748b", justify="left", anchor="w",
        ).pack(anchor="w", pady=(0, 8))

        sync_btn = ttk.Button(action_frame, text="Run Full O3 Sync", command=self.run_sync)
        sync_btn.pack(anchor="w", pady=(2, 4))
        self.buttons.append(sync_btn)

        spacer = ttk.Frame(view)
        spacer.grid(row=2, column=0, sticky="nsew")
        view.rowconfigure(2, weight=1)
        return view

    def go_back(self):
        self.on_back_to_menu()
        self.destroy()

    def log(self, message: str) -> None:
        self.log_panel.write(message)

    def log_thread(self, message: str) -> None:
        self.after(0, lambda: self.log(message))

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in self.buttons:
            try:
                button.configure(state=state)
            except tk.TclError:
                pass
        if not busy:
            for combo in self._visit_type_combos:
                combo.configure(state="readonly")

    def browse_output_dir(self) -> None:
        selected = filedialog.askdirectory(title="Select output folder for exports and mappings")
        if selected:
            self.output_dir_var.set(selected)
            self._apply_default_paths(selected)

    def browse_export_output(self) -> None:
        visit_type_name = self.visit_type_var.get().strip()
        initial_name = f"openmrs3_export_{sanitize_filename(visit_type_name)}.csv" if visit_type_name else "openmrs3_export.csv"
        selected = filedialog.asksaveasfilename(
            title="Choose O3 export output file",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=initial_name,
        )
        if selected:
            self.export_file_var.set(selected)
            self.input_csv_var.set(selected)

    def browse_transform_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="Choose transformed DHIS2 output file",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="dhis2_tracker_import.csv",
        )
        if selected:
            self.transform_output_var.set(selected)
            self.import_file_var.set(selected)

    def browse_input_csv(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose the O3 export CSV to transform",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.input_csv_var.set(selected)

    def browse_import_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose the transformed DHIS2 tracker CSV to import",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.import_file_var.set(selected)

    def _apply_default_paths(self, output_dir: str) -> None:
        base = Path(output_dir)
        if not self.export_file_var.get().strip():
            self.export_file_var.set(str(base / "openmrs3_export.csv"))
        if not self.input_csv_var.get().strip():
            self.input_csv_var.set(str(base / "openmrs3_export.csv"))
        if not self.transform_output_var.get().strip():
            self.transform_output_var.set(str(base / "dhis2_tracker_import.csv"))
        if not self.import_file_var.get().strip():
            self.import_file_var.set(str(base / "dhis2_tracker_import.csv"))

    def _create_api(self) -> ApiClient:
        base_url = normalize_base_url(self.base_url_var.get())
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            raise ValueError("Username and password are required.")
        api = ApiClient(base_url=base_url, username=username, password=password)
        try:
            api.session_ok = api.login_session()
        except Exception:
            api.session_ok = False
            api.get_json(f"{api.base_url}/session")
        return api

    def _output_dir(self) -> Path:
        path = Path(self.output_dir_var.get().strip())
        if not path.name:
            raise RuntimeError("Please choose an output folder.")
        return path

    def _visit_type_selection(self) -> Tuple[str, Dict]:
        visit_type_name = self.visit_type_var.get().strip()
        if not visit_type_name:
            raise RuntimeError("Load visit types and choose one before exporting.")
        selected_visit = next(
            (vt for vt in self.visit_types if vt.get("name") == visit_type_name),
            None,
        )
        if not selected_visit:
            raise RuntimeError(f"Visit type '{visit_type_name}' was not found on the server.")
        return visit_type_name, selected_visit

    def _dhis2_credentials(self) -> Tuple[str, str, str]:
        dhis2_url = self.dhis2_url_var.get().strip()
        dhis2_username = self.dhis2_username_var.get().strip()
        dhis2_password = self.dhis2_password_var.get()
        if not dhis2_url or not dhis2_username or not dhis2_password:
            raise RuntimeError("Enter the DHIS2 URL, username, and password.")
        return dhis2_url, dhis2_username, dhis2_password

    def connect_and_load(self) -> None:
        if self.busy:
            return

        def worker() -> None:
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Connecting to OpenMRS 3 and loading visit types..."))
            try:
                api = self._create_api()
                visit_types = sorted(
                    api.get_visit_types(),
                    key=lambda item: str(item.get("name", "")).lower(),
                )
                names = [vt.get("name", "") for vt in visit_types if vt.get("name")]
                if not names:
                    raise RuntimeError("No visit types were returned by this OpenMRS server.")

                def on_success() -> None:
                    self.api = api
                    self.visit_types = visit_types
                    for combo in self._visit_type_combos:
                        combo["values"] = names
                    self.visit_type_var.set(names[0])
                    self.status_var.set(f"Connected to {api.base_url}. Loaded {len(names)} visit types.")
                    self.log(f"Connected to {api.base_url}")
                    self.log(f"Loaded {len(names)} visit types.")
                    self.set_busy(False)

                self.after(0, on_success)
            except Exception as exc:
                self.after(0, lambda exc=exc: self._handle_error("Connection failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _load_form_registry(self) -> FormRegistry:
        if not O3_SCHEMA_ROOT.is_dir():
            self.log_thread(
                f"Warning: O3 form schemas folder not found at {O3_SCHEMA_ROOT}; "
                "obs columns will use backend concept names instead of form labels."
            )
            return FormRegistry([])
        registry = load_default_forms(O3_SCHEMA_ROOT)
        self.log_thread(f"Loaded {len(registry.forms)} form schemas from {O3_SCHEMA_ROOT}.")
        return registry

    def _run_export(self) -> Tuple[Path, str, int]:
        start_date = normalize_date_filter(self.start_date_var.get())
        end_date = normalize_date_filter(self.end_date_var.get())
        validate_date_range(start_date, end_date)
        visit_type_name, selected_visit = self._visit_type_selection()
        program_value = determine_program_from_visit_type(visit_type_name)
        if not program_value:
            raise RuntimeError(
                "Could not determine the DHIS2 program from the visit type. Use a visit type "
                "containing 'Delivery'/'Labour' for maternal or 'NICU' for neonatal data."
            )
        output_file_str = self.export_file_var.get().strip()
        output_path = Path(output_file_str) if output_file_str else (self._output_dir() / "openmrs3_export.csv")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        registry = self._load_form_registry()

        api = self.api or self._create_api()
        if not self.visit_types:
            self.visit_types = api.get_visit_types()
        visits = api.get_visits(
            visit_start_date=start_date,
            visit_end_date=end_date,
            visit_type_uuid=selected_visit["uuid"],
        )
        patients = get_patients_by_visit_type(
            visits=visits,
            visit_type_uuid=selected_visit["uuid"],
            visit_start_date=start_date,
            visit_end_date=end_date,
        )
        if not patients:
            raise RuntimeError("No patients matched the selected visit type and date range.")
        self.log_thread(f"Matched {len(patients)} patients for '{visit_type_name}'.")

        count = write_o3_patients_csv(
            api=api,
            registry=registry,
            patients=patients,
            output_filename=output_path,
            org_unit_code=self._selected_facility_code(),
            program_value=program_value,
            fetch_concurrency=12,
        )
        self.api = api
        self.input_csv_var.set(str(output_path))
        return output_path, program_value, count

    def export_patients(self) -> None:
        if self.busy:
            return

        def worker() -> None:
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Exporting O3 patients..."))
            try:
                output_path, _program_value, count = self._run_export()

                def on_success() -> None:
                    self.status_var.set(f"Export complete. {count} patients written.")
                    self.log(f"Exported {count} patients to {output_path}")
                    self.set_busy(False)
                    messagebox.showinfo("Export complete", f"Exported {count} patients to:\n{output_path}")

                self.after(0, on_success)
            except Exception as exc:
                self.after(0, lambda exc=exc: self._handle_error("Export failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _stored_mapping_paths(self) -> Tuple[Path, Path, Path]:
        return (
            O3_MAPPING_DIR / "EMR-DHIS2 Tracker O3 Maternal Mapping.xlsx",
            O3_MAPPING_DIR / "EMR-DHIS2 Tracker O3 Neonatal Mapping.xlsx",
            O3_MAPPING_DIR / "EMR-DHIS2 Tracker O3 Value Mappings.csv",
        )

    def load_mapping_files(self) -> None:
        if self.busy:
            return
        try:
            maternal_path, neonatal_path, value_path = self._stored_mapping_paths()
            missing = [str(path) for path in (maternal_path, neonatal_path, value_path) if not path.is_file()]
            if missing:
                raise RuntimeError(
                    "Stored O3 mapping files are missing:\n" + "\n".join(missing) +
                    "\n\nGenerate them once and store them in Resources/O3, or place the files there."
                )
            maternal_rows = max(0, len(read_xlsx_rows(maternal_path)) - 1)
            neonatal_rows = max(0, len(read_xlsx_rows(neonatal_path)) - 1)
            value_rows = max(0, sum(1 for _ in value_path.open()) - 1)
            self.status_var.set("O3 mapping files verified in Resources/O3.")
            self.log(f"Loaded stored O3 mapping files from {O3_MAPPING_DIR}")
            self.log(f"  Maternal mapping rows: {maternal_rows}")
            self.log(f"  Neonatal mapping rows: {neonatal_rows}")
            self.log(f"  Value mapping rows: {value_rows}")
        except Exception as exc:
            self._handle_error("Loading mapping files failed", exc)

    def _pick_o3_files(self, program_value: str) -> Tuple[Path, Path, Path]:
        maternal_path, neonatal_path, value_path = self._stored_mapping_paths()
        if program_value == MATERNAL_PROGRAM:
            mapping_path = maternal_path
            dictionary_path = DEFAULT_MATERNAL_DICTIONARY
            if not dictionary_path.is_file():
                dictionary_path = Path("Resources/MID data disctionary.xlsx")
        else:
            mapping_path = neonatal_path
            dictionary_path = DEFAULT_NEONATAL_DICTIONARY
            if not dictionary_path.is_file():
                dictionary_path = Path("Resources/NCF data disctionary.xlsx")
        for path in (mapping_path, dictionary_path, value_path):
            if not path.is_file():
                raise RuntimeError(
                    f"Required file not found: {path}. "
                    "Load the stored O3 mapping files from Resources/O3 first."
                )
        return mapping_path, dictionary_path, value_path

    def _run_transform(self, input_path: Path) -> Tuple[Path, int, Dict[str, int], Dict[str, List[str]]]:
        program_value = self._export_program(input_path)
        mapping_path, dictionary_path, value_mapping_path = self._pick_o3_files(program_value)
        output_file_str = self.transform_output_var.get().strip()
        output_path = Path(output_file_str) if output_file_str else (self._output_dir() / "dhis2_tracker_import.csv")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        set_mapping_files(mapping_path, dictionary_path, value_mapping_path)
        row_count, counts, missing = transform_rows(input_path, output_path)
        self.import_file_var.set(str(output_path))
        return output_path, row_count, counts, missing

    def transform_export(self) -> None:
        if self.busy:
            return

        input_path = Path(self.input_csv_var.get().strip() or self._output_dir() / "openmrs3_export.csv")
        if not input_path.is_file():
            messagebox.showerror(
                "Export file not found",
                f"No O3 export found at {input_path}. Choose an export CSV first.",
            )
            return

        def worker() -> None:
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Transforming O3 export..."))
            try:
                output_path, row_count, counts, missing = self._run_transform(input_path)

                def on_success() -> None:
                    self.status_var.set(f"Transformation complete. {row_count} row(s) processed.")
                    self.log(f"Transformed {row_count} row(s) from {input_path.name}.")
                    self._log_missing(missing)
                    self.log(f"Transformed output saved to: {output_path}")
                    self.set_busy(False)
                    messagebox.showinfo(
                        "Transformation complete",
                        f"Processed {row_count} row(s). Output saved to:\n{output_path}",
                    )

                self.after(0, on_success)
            except Exception as exc:
                self.after(0, lambda exc=exc: self._handle_error("Transformation failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def import_transformed(self) -> None:
        if self.busy:
            return
        try:
            dhis2_url, dhis2_username, dhis2_password = self._dhis2_credentials()
        except RuntimeError as exc:
            messagebox.showerror("DHIS2 details required", str(exc))
            return

        input_path = Path(self.import_file_var.get().strip() or self._output_dir() / "dhis2_tracker_import.csv")
        if not input_path.is_file():
            messagebox.showerror(
                "Transformed file not found",
                f"No transformed O3 file found at {input_path}. Choose a transformed CSV first.",
            )
            return

        def worker() -> None:
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Importing to DHIS2..."))
            try:
                counts = import_rows(
                    base_url=dhis2_url,
                    username=dhis2_username,
                    password=dhis2_password,
                    input_path=input_path,
                )

                def on_success() -> None:
                    self.status_var.set(f"Import done. {counts['processed']} row(s) processed.")
                    self.log(f"Imported {input_path.name}.")
                    self._log_import_counts(counts)
                    self.set_busy(False)
                    messagebox.showinfo(
                        "Import complete",
                        f"Processed {counts['processed']} row(s).\nLog: {counts['log_file']}",
                    )

                self.after(0, on_success)
            except Exception as exc:
                self.after(0, lambda exc=exc: self._handle_error("Import failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def run_sync(self) -> None:
        if self.busy:
            return
        try:
            dhis2_url, dhis2_username, dhis2_password = self._dhis2_credentials()
            if not self.username_var.get().strip() or not self.password_var.get():
                raise RuntimeError("Enter the EMR username and password.")
        except RuntimeError as exc:
            messagebox.showerror("Sync details required", str(exc))
            return

        def worker() -> None:
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Running full O3 sync (export -> transform -> import)..."))
            try:
                self.log_thread("Step 1/3: Exporting O3 patients...")
                export_path, _program_value, count = self._run_export()
                self.log_thread(f"  Exported {count} patient(s) to {export_path}")

                self.log_thread("Step 2/3: Using stored O3 mapping files...")
                maternal_path, neonatal_path, value_path = self._stored_mapping_paths()
                for path in (maternal_path, neonatal_path, value_path):
                    if not path.is_file():
                        raise RuntimeError(f"Stored mapping file not found: {path}.")
                self.log_thread(f"  Mapping files loaded from {O3_MAPPING_DIR}")

                self.log_thread("Step 3/3: Transforming and importing to DHIS2...")
                output_path, row_count, _counts, missing = self._run_transform(export_path)
                self.log_thread(f"  Transformed {row_count} row(s).")
                self._log_missing(missing)
                report_path = self._missing_report_path(output_path)
                counts = import_rows(
                    base_url=dhis2_url,
                    username=dhis2_username,
                    password=dhis2_password,
                    input_path=output_path,
                )

                def on_success() -> None:
                    self.status_var.set(f"Sync complete. {counts['processed']} row(s) imported.")
                    self._log_import_counts(counts)
                    self.log(f"  Unmatched fields report: {report_path}")
                    self.set_busy(False)
                    messagebox.showinfo(
                        "Sync complete",
                        f"Exported {count} patient(s), imported {counts['processed']} row(s).\n"
                        f"Report: {report_path}",
                    )

                self.after(0, on_success)
            except Exception as exc:
                self.after(0, lambda exc=exc: self._handle_error("Sync failed", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _export_program(self, input_path: Path) -> str:
        from config import normalize_program_value

        with input_path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or "program" not in reader.fieldnames:
                raise RuntimeError("The O3 export does not contain a 'program' column.")
            row = next(reader, None)
            if row is None:
                raise RuntimeError("The O3 export has no data rows.")
            program_value = normalize_program_value(row.get("program", ""))
        if program_value not in (MATERNAL_PROGRAM, NEONATAL_PROGRAM):
            raise RuntimeError(f"Unrecognized program value in export: {row.get('program', '')!r}")
        return program_value

    def _missing_report_path(self, output_path: Path) -> Path:
        return output_path.with_name(output_path.stem + "_missing_fields.csv")

    def _log_missing(self, missing: Dict[str, List[str]]) -> None:
        for program, fields in missing.items():
            if fields:
                self.log(f"  {program}: {len(fields)} mapped field(s) could not be matched.")

    def _log_import_counts(self, counts: Dict[str, object]) -> None:
        self.log(f"  Rows processed: {counts['processed']}")
        self.log(f"  Tracked entities created: {counts['created_entities']}")
        self.log(f"  Tracked entities updated: {counts['updated_entities']}")
        self.log(f"  Enrollments created: {counts['created_enrollments']}")
        self.log(f"  Events created or updated: {counts['upserted_events']}")
        self.log(f"  Values discarded: {counts['unsynced_values']}")
        if counts["row_errors"]:
            self.log(f"  Rows with import errors: {counts['row_errors']}")
        self.log(f"  Import value log: {counts['log_file']}")
        if counts["skipped"]:
            self.log(f"  Rows skipped: {counts['skipped']}")

    def _handle_error(self, title: str, exc: Exception) -> None:
        self.status_var.set(f"{title}: {exc}")
        self.log(f"{title}: {exc}")
        self.set_busy(False)
        messagebox.showerror(title, str(exc))