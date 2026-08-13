from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from import_.importer import import_rows


class ImportPage(ttk.Frame):
    def __init__(self, parent, on_back_to_menu):
        super().__init__(parent)
        self.parent = parent
        self.on_back_to_menu = on_back_to_menu

        self.url_var = tk.StringVar(value="https://imnid.aau.edu.et/dhis")
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.file_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value="Enter your DHIS2 connection details, choose the transformed CSV, then import."
        )
        self.import_in_progress = False

        self._build_ui()

    def _build_ui(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 18))
        header.pack(fill="x")

        back_btn = ttk.Button(header, text="\u2190 Back to Main Menu", command=self.go_back)
        back_btn.pack(side="left")

        ttk.Label(
            header,
            text="Import to DHIS2",
            style="Header.TLabel",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="center")

        ttk.Label(
            header,
            text="Import a transformed tracker CSV into DHIS2.",
            style="Header.TLabel",
            font=("Segoe UI", 10),
        ).pack(anchor="center", pady=(4, 0))

        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(container, text="DHIS2 URL").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(container, textvariable=self.url_var, width=60).grid(
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
        ttk.Label(container, text="Transformed CSV").grid(row=row, column=0, sticky="w", pady=4)
        file_frame = ttk.Frame(container)
        file_frame.grid(row=row, column=1, sticky="ew", pady=4)
        file_frame.columnconfigure(0, weight=1)
        ttk.Entry(file_frame, textvariable=self.file_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(file_frame, text="Browse", command=self.browse_file).grid(
            row=0, column=1, padx=(8, 0)
        )

        row += 1
        self.import_button = ttk.Button(
            container,
            text="Import to DHIS2",
            command=self.import_file,
        )
        self.import_button.grid(row=row, column=1, sticky="w", pady=(10, 12))

        row += 1
        ttk.Label(container, textvariable=self.status_var, foreground="#1f4e79").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        row += 1
        log_frame = ttk.LabelFrame(container, text="Import Log", padding=10)
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
        self.import_button.configure(state="disabled" if busy else "normal")

    def browse_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose the transformed DHIS2 tracker CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if selected:
            self.file_var.set(selected)

    def import_file(self) -> None:
        if self.import_in_progress:
            return

        base_url = self.url_var.get().strip()
        username = self.username_var.get().strip()
        password = self.password_var.get()
        input_path = Path(self.file_var.get().strip())

        if not base_url:
            messagebox.showerror("URL required", "Please enter the DHIS2 server URL.")
            return
        if not username or not password:
            messagebox.showerror("Credentials required", "Please enter DHIS2 username and password.")
            return
        if not input_path.is_file():
            messagebox.showerror("File required", "Please choose a valid transformed CSV file.")
            return

        def worker() -> None:
            self.import_in_progress = True
            self.after(0, lambda: self.set_busy(True))
            self.after(0, lambda: self.status_var.set("Importing to DHIS2..."))
            try:
                counts = import_rows(
                    base_url=base_url,
                    username=username,
                    password=password,
                    input_path=input_path,
                )

                def on_success() -> None:
                    self.status_var.set(f"Import done. {counts['processed']} row(s) processed.")
                    self.log(f"Import complete.")
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
                    self.set_busy(False)
                    self.import_in_progress = False
                    messagebox.showinfo(
                        "Import complete",
                        f"Processed {counts['processed']} row(s).\nLog: {counts['log_file']}",
                    )

                self.after(0, on_success)
            except Exception as exc:
                self.after(
                    0,
                    lambda exc=exc: self._handle_error("Import failed", exc),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _handle_error(self, title: str, exc: Exception) -> None:
        self.status_var.set(f"{title}: {exc}")
        self.log(f"{title}: {exc}")
        self.set_busy(False)
        self.import_in_progress = False
        messagebox.showerror(title, str(exc))
