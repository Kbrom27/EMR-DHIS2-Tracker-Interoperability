from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from clients.openmrs_client import ApiClient, normalize_base_url
from config import FACILITIES, FACILITY_CODES
from export.extractors import (
    determine_program_from_visit_type,
    get_patients_by_visit_type,
    normalize_date_filter,
    sanitize_filename,
    validate_date_range,
    write_patients_csv,
)
from ui.components import LogPanel


class ExportPage(ttk.Frame):
    def __init__(self, parent, on_back_to_menu):
        super().__init__(parent)
        self.parent = parent
        self.on_back_to_menu = on_back_to_menu

        self.api: Optional[ApiClient] = None
        self.visit_types: List[Dict] = []
        self.export_in_progress = False

        self.base_url_var = tk.StringVar()
        self.username_var = tk.StringVar(value="superman")
        self.password_var = tk.StringVar(value="Admin123")
        self.facility_var = tk.StringVar(value=FACILITIES[0][0])
        self.start_date_var = tk.StringVar()
        self.end_date_var = tk.StringVar()
        self.visit_type_var = tk.StringVar()
        self.output_var = tk.StringVar(
            value=str(Path(__file__).resolve().with_name("openmrs_export.csv"))
        )
        self.status_var = tk.StringVar(
            value="Enter OpenMRS connection details, then load visit types."
        )

        self._build_ui()

    def _build_ui(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 18))
        header.pack(fill="x")

        back_btn = ttk.Button(header, text="\u2190 Back to Main Menu", command=self.go_back)
        back_btn.pack(side="left")

        ttk.Label(
            header,
            text="EMR Data Export",
            style="Header.TLabel",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="center")

        ttk.Label(
            header,
            text="Export OpenMRS patient data by visit type and date range.",
            style="Header.TLabel",
            font=("Segoe UI", 10),
        ).pack(anchor="center", pady=(4, 0))

        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(container, text="EMR Server / IP").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(container, textvariable=self.base_url_var, width=60).grid(
            row=row, column=1, sticky="ew", pady=4
        )

        row += 1
        ttk.Label(container, text="Username").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(container, textvariable=self.username_var, width=30).grid(
            row=row, column=1, sticky="w", pady=4
        )

        row += 1
        ttk.Label(container, text="Password").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(container, textvariable=self.password_var, show="*", width=30).grid(
            row=row, column=1, sticky="w", pady=4
        )

        row += 1
        ttk.Label(container, text="Facility").grid(row=row, column=0, sticky="w", pady=4)
        self.facility_combo = ttk.Combobox(
            container,
            textvariable=self.facility_var,
            state="readonly",
            width=57,
            values=[name for name, _code in FACILITIES],
        )
        self.facility_combo.grid(row=row, column=1, sticky="w", pady=4)

        row += 1
        button_row = ttk.Frame(container)
        button_row.grid(row=row, column=1, sticky="w", pady=(6, 10))
        self.connect_button = ttk.Button(
            button_row,
            text="Connect and Load Visit Types",
            command=self.load_visit_types,
        )
        self.connect_button.pack(side="left")

        row += 1
        ttk.Label(container, text="Start Date").grid(row=row, column=0, sticky="w", pady=4)
        date_frame = ttk.Frame(container)
        date_frame.grid(row=row, column=1, sticky="w", pady=4)
        ttk.Entry(date_frame, textvariable=self.start_date_var, width=14).pack(side="left")
        ttk.Label(date_frame, text="YYYY-MM-DD").pack(side="left", padx=(8, 18))
        ttk.Label(date_frame, text="End Date").pack(side="left")
        ttk.Entry(date_frame, textvariable=self.end_date_var, width=14).pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(date_frame, text="YYYY-MM-DD").pack(side="left", padx=(8, 0))

        row += 1
        ttk.Label(container, text="Visit Type").grid(row=row, column=0, sticky="w", pady=4)
        self.visit_type_combo = ttk.Combobox(
            container,
            textvariable=self.visit_type_var,
            state="readonly",
            width=57,
        )
        self.visit_type_combo.grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        ttk.Label(container, text="Output CSV").grid(row=row, column=0, sticky="w", pady=4)
        output_frame = ttk.Frame(container)
        output_frame.grid(row=row, column=1, sticky="ew", pady=4)
        output_frame.columnconfigure(0, weight=1)
        ttk.Entry(output_frame, textvariable=self.output_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_frame, text="Browse", command=self.browse_output).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        self.export_button = ttk.Button(
            container,
            text="Export Patients",
            command=self.export_patients,
        )
        self.export_button.grid(row=row, column=1, sticky="w", pady=(10, 12))

        row += 1
        ttk.Label(container, textvariable=self.status_var, foreground="#1f4e79").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        row += 1
        log_frame = ttk.LabelFrame(container, text="Export Log", padding=10)
        log_frame.grid(row=row, column=0, columnspan=2, sticky="nsew")
        container.rowconfigure(row, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=18, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def go_back(self):
        self.on_back_to_menu()
        self.destroy()

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.connect_button.configure(state=state)
        self.export_button.configure(state=state)
        if not busy:
            self.visit_type_combo.configure(state="readonly")

    def browse_output(self) -> None:
        visit_type_name = self.visit_type_var.get().strip()
        if visit_type_name:
            initial_name = sanitize_filename(visit_type_name) + ".csv"
        else:
            initial_name = "openmrs_export.csv"
        selected = filedialog.asksaveasfilename(
            title="Choose export file",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=initial_name,
        )
        if selected:
            self.output_var.set(selected)

    def _selected_facility_code(self) -> str:
        facility_name = self.facility_var.get().strip()
        return FACILITY_CODES.get(facility_name, facility_name)

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

    def load_visit_types(self) -> None:
        if self.export_in_progress:
            return

        def worker() -> None:
            self.after(
                0,
                lambda: self.status_var.set("Connecting to OpenMRS and loading visit types..."),
            )
            self.after(0, lambda: self.set_busy(True))
            try:
                api = self._create_api()
                visit_types = sorted(
                    api.get_visit_types(),
                    key=lambda item: str(item.get("name", "")).lower(),
                )
                visit_type_names = [
                    visit_type.get("name", "") for visit_type in visit_types if visit_type.get("name")
                ]
                if not visit_type_names:
                    raise RuntimeError("No visit types were returned by this OpenMRS server.")

                def on_success() -> None:
                    self.api = api
                    self.visit_types = visit_types
                    self.visit_type_combo["values"] = visit_type_names
                    self.visit_type_var.set(visit_type_names[0])
                    self.status_var.set(f"Connected. Loaded {len(visit_type_names)} visit types.")
                    self.log(f"Connected to {api.base_url}")
                    self.log(f"Loaded {len(visit_type_names)} visit types from OpenMRS.")
                    self.set_busy(False)

                self.after(0, on_success)
            except Exception as exc:
                self.after(
                    0,
                    lambda exc=exc: self._handle_error("Connection failed", exc),
                )

        threading.Thread(target=worker, daemon=True).start()

    def export_patients(self) -> None:
        if self.export_in_progress:
            return

        try:
            start_date = normalize_date_filter(self.start_date_var.get())
            end_date = normalize_date_filter(self.end_date_var.get())
            validate_date_range(start_date, end_date)
        except ValueError as exc:
            messagebox.showerror("Invalid date", str(exc))
            return

        visit_type_name = self.visit_type_var.get().strip()
        if not visit_type_name:
            messagebox.showerror(
                "Visit type required",
                "Load visit types and choose one before exporting.",
            )
            return

        output_path = Path(self.output_var.get().strip())
        if not output_path.name:
            messagebox.showerror(
                "Output file required",
                "Choose where to save the CSV export.",
            )
            return

        org_unit_code = self._selected_facility_code()
        program_value = determine_program_from_visit_type(visit_type_name)

        def worker() -> None:
            self.export_in_progress = True
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Export in progress..."))
            try:
                api = self.api or self._create_api()
                if not self.visit_types:
                    self.visit_types = api.get_visit_types()

                selected_visit = next(
                    (
                        visit_type
                        for visit_type in self.visit_types
                        if visit_type.get("name") == visit_type_name
                    ),
                    None,
                )
                if not selected_visit:
                    raise RuntimeError(
                        f"Visit type '{visit_type_name}' was not found on the current server."
                    )

                self.after(
                    0,
                    lambda: self.log(
                        f"Loading visits for '{visit_type_name}'"
                        + (f" from {start_date}" if start_date else "")
                        + (f" to {end_date}" if end_date else "")
                        + "..."
                    ),
                )

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
                    raise RuntimeError(
                        "No patients matched the selected visit type and date range."
                    )

                self.after(
                    0,
                    lambda: self.log(
                        f"Matched {len(patients)} patients with visit type '{visit_type_name}'. "
                        "Their full observations, diagnoses, medications, and orders will be merged into the export."
                    ),
                )

                exported_count = write_patients_csv(
                    api=api,
                    patients=patients,
                    output_filename=output_path,
                    org_unit_code=org_unit_code,
                    program_value=program_value,
                    fetch_concurrency=12,
                )

                def on_success() -> None:
                    self.api = api
                    self.status_var.set(
                        f"Export complete. {exported_count} patients written."
                    )
                    self.log(
                        f"Export finished: {exported_count} patients written to {output_path}"
                    )
                    self.set_busy(False)
                    self.export_in_progress = False
                    messagebox.showinfo(
                        "Export complete",
                        f"Exported {exported_count} patients to:\n{output_path}",
                    )

                self.after(0, on_success)
            except Exception as exc:
                self.after(
                    0,
                    lambda exc=exc: self._handle_error("Export failed", exc),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _handle_error(self, title: str, exc: Exception) -> None:
        self.status_var.set(f"{title}: {exc}")
        self.log(f"{title}: {exc}")
        self.set_busy(False)
        self.export_in_progress = False
        messagebox.showerror(title, str(exc))
