from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from transform.mapping import set_mapping_files
from transform.pipeline import transform_rows
from ui.components import LogPanel


class TransformPage(ttk.Frame):
    def __init__(self, parent, on_back_to_menu):
        super().__init__(parent)
        self.parent = parent
        self.on_back_to_menu = on_back_to_menu

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(
            value=str(Path(__file__).resolve().with_name("dhis2_tracker_import.csv"))
        )
        self.mapping_var = tk.StringVar()
        self.dict_var = tk.StringVar()
        self.value_mapping_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value="Select mapping files, choose the OpenMRS export CSV, then transform."
        )
        self.transform_in_progress = False

        self._build_ui()

    def _build_ui(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 18))
        header.pack(fill="x")

        back_btn = ttk.Button(header, text="\u2190 Back to Main Menu", command=self.go_back)
        back_btn.pack(side="left")

        ttk.Label(
            header,
            text="Transform CSV",
            style="Header.TLabel",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="center")

        ttk.Label(
            header,
            text="Convert an OpenMRS export CSV into DHIS2 tracker CSV using mapping files.",
            style="Header.TLabel",
            font=("Segoe UI", 10),
        ).pack(anchor="center", pady=(4, 0))

        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(container, text="OpenMRS Export CSV").grid(row=row, column=0, sticky="w", pady=4)
        input_frame = ttk.Frame(container)
        input_frame.grid(row=row, column=1, sticky="ew", pady=4)
        input_frame.columnconfigure(0, weight=1)
        ttk.Entry(input_frame, textvariable=self.input_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(input_frame, text="Browse", command=self.browse_input).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        ttk.Label(container, text="Mapping Excel File").grid(row=row, column=0, sticky="w", pady=4)
        mapping_frame = ttk.Frame(container)
        mapping_frame.grid(row=row, column=1, sticky="ew", pady=4)
        mapping_frame.columnconfigure(0, weight=1)
        ttk.Entry(mapping_frame, textvariable=self.mapping_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(mapping_frame, text="Browse", command=self.browse_mapping).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        ttk.Label(container, text="Dictionary Excel File").grid(row=row, column=0, sticky="w", pady=4)
        dict_frame = ttk.Frame(container)
        dict_frame.grid(row=row, column=1, sticky="ew", pady=4)
        dict_frame.columnconfigure(0, weight=1)
        ttk.Entry(dict_frame, textvariable=self.dict_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(dict_frame, text="Browse", command=self.browse_dictionary).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        ttk.Label(container, text="Value Mapping CSV (Optional)").grid(row=row, column=0, sticky="w", pady=4)
        value_frame = ttk.Frame(container)
        value_frame.grid(row=row, column=1, sticky="ew", pady=4)
        value_frame.columnconfigure(0, weight=1)
        ttk.Entry(value_frame, textvariable=self.value_mapping_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(value_frame, text="Browse", command=self.browse_value_mapping).grid(
            row=0, column=1, padx=(8, 0)
        )

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
        self.transform_button = ttk.Button(
            container,
            text="Transform CSV",
            command=self.transform_file,
        )
        self.transform_button.grid(row=row, column=1, sticky="w", pady=(10, 12))

        row += 1
        ttk.Label(container, textvariable=self.status_var, foreground="#1f4e79").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        row += 1
        log_frame = ttk.LabelFrame(container, text="Transformation Log", padding=10)
        log_frame.grid(row=row, column=0, columnspan=2, sticky="nsew")
        container.rowconfigure(row, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=20, state="disabled")
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
        self.transform_button.configure(state="disabled" if busy else "normal")

    def browse_input(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose the exported OpenMRS CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.input_var.set(selected)
            input_path = Path(selected)
            if not self.output_var.get().strip():
                self.output_var.set(str(input_path.with_name(f"{input_path.stem}_dhis2.csv")))

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

    def browse_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="Choose transformed CSV file",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="dhis2_tracker_import.csv",
        )
        if selected:
            self.output_var.set(selected)

    def transform_file(self) -> None:
        if self.transform_in_progress:
            return

        mapping_path = Path(self.mapping_var.get().strip())
        dict_path = Path(self.dict_var.get().strip())
        input_path = Path(self.input_var.get().strip())
        output_path = Path(self.output_var.get().strip())
        value_mapping_path = Path(self.value_mapping_var.get().strip()) if self.value_mapping_var.get().strip() else None

        if not mapping_path.is_file():
            messagebox.showerror("Mapping file required", "Please select a valid mapping Excel file.")
            return
        if not dict_path.is_file():
            messagebox.showerror("Dictionary file required", "Please select a valid dictionary Excel file.")
            return
        if not input_path.is_file():
            messagebox.showerror("Input file required", "Choose a valid OpenMRS export CSV file.")
            return
        if not output_path.name:
            messagebox.showerror("Output file required", "Choose where to save the transformed CSV.")
            return

        set_mapping_files(mapping_path, dict_path, value_mapping_path)

        if value_mapping_path and value_mapping_path.exists():
            self.log(f"Loaded value mappings from: {value_mapping_path}")
        elif value_mapping_path:
            self.log(f"Warning: Value mapping file not found at {value_mapping_path}")
        else:
            self.log("No value mapping file provided (optional)")

        def worker() -> None:
            self.transform_in_progress = True
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Transforming CSV for DHIS2 tracker..."))
            try:
                row_count, counts, missing_fields = transform_rows(input_path, output_path)
                maternal_count = counts.get("Maternal Inpatient Data/aLoraiFNkng", 0)
                neonatal_count = counts.get("Neonatal Care Form/QYJKpoUeg9F", 0)
                skipped = counts.get("skipped", 0)

                def on_success() -> None:
                    self.status_var.set(f"Transformation complete. {row_count} row(s) processed.")
                    self.log(f"Done. {row_count} row(s) processed in total.")
                    if maternal_count:
                        self.log(f"  Maternal rows: {maternal_count}")
                    if neonatal_count:
                        self.log(f"  Neonatal rows: {neonatal_count}")
                    if skipped:
                        self.log(f"  Skipped: {skipped}")
                    for program, fields in missing_fields.items():
                        if fields:
                            self.log(f"{program}: {len(fields)} mapped field(s) could not be matched.")
                    self.set_busy(False)
                    self.transform_in_progress = False
                    messagebox.showinfo(
                        "Transformation complete",
                        f"Processed {row_count} row(s). Output saved to:\n{output_path}",
                    )

                self.after(0, on_success)
            except Exception as exc:
                self.after(
                    0,
                    lambda exc=exc: self._handle_error("Transformation failed", exc),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _handle_error(self, title: str, exc: Exception) -> None:
        self.status_var.set(f"{title}: {exc}")
        self.log(f"{title}: {exc}")
        self.set_busy(False)
        self.transform_in_progress = False
        messagebox.showerror(title, str(exc))
