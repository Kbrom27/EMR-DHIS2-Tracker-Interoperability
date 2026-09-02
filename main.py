#!/usr/bin/env python3
"""
EMR-DHIS2 Tracker Interoperability - Main Application

The app presents two completely separate workflows that do not share scripts:

1. OpenMRS (Bahmni)  - the original workflow. Uses the imported/uploaded
   mapping Excel files and dictionary that the user selects. Sub-menus:
   EMR Data Export, Transform CSV, Import to DHIS2, EMR-DHIS2 Tracker Sync.
   Backed by the self-contained modules in export/, transform/, import_/,
   clients/, rules/, config.py, utils.py and models.py.

2. OpenMRS 3 (O3)    - the OpenMRS 3 workflow. Does NOT ask the user to
   upload mapping files; it uses stored/generated mapping files. Backed by
   the self-contained o3app/ package (its own copies of config, utils,
   models, clients, export, transform, import_, rules, plus the O3
   extraction and mapping generation) and o3app/ui/o3_page.py.
"""

import tkinter as tk
from tkinter import ttk

from ui.export_page import ExportPage
from ui.transform_page import TransformPage
from ui.import_page import ImportPage
from ui.sync_page import SyncPage
from ui.mediator_page import MediatorPage
from ui.bahmni_page import BahmniPage
from o3app.ui.o3_page import O3Page


APP_TITLE = "EMR-DHIS2 Tracker Interoperability V102"


class MainApplication:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x780")
        self.root.minsize(980, 680)

        self.current_page = None
        self.menu_frame = None
        self.configure_style()
        self.show_source_menu()

    def configure_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#eef4f8")
        style.configure("Header.TFrame", background="#14324a")
        style.configure("Header.TLabel", background="#14324a", foreground="white")
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Status.TLabel", background="#eef4f8", foreground="#0f5132")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("Primary.TButton", font=("Segoe UI", 11, "bold"), padding=(18, 10))

    def clear_page(self):
        if self.current_page:
            self.current_page.destroy()
        self.current_page = None

    def hide_menu(self):
        if self.menu_frame:
            self.menu_frame.destroy()
            self.menu_frame = None

    def _build_header(self, parent, title, subtitle):
        header = ttk.Frame(parent, style="Header.TFrame", padding=(22, 18))
        header.pack(fill="x")
        ttk.Label(
            header,
            text=title,
            style="Header.TLabel",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=subtitle,
            style="Header.TLabel",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))
        return header

    def create_tile(self, parent, title, subtitle, color, command, row, col, colspan=1):
        tile = tk.Frame(parent, bg=color, padx=20, pady=18, cursor="hand2")
        tile.grid(row=row, column=col, columnspan=colspan, sticky="nsew", padx=10, pady=10)
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
            wraplength=780 if colspan > 1 else 390,
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
        tile.bind("<Button-1>", lambda e, cmd=command: cmd())

    def show_source_menu(self):
        self.clear_page()
        self.hide_menu()

        self.menu_frame = ttk.Frame(self.root)
        self.menu_frame.pack(fill="both", expand=True)

        self._build_header(
            self.menu_frame,
            APP_TITLE,
            "Choose the OpenMRS source or start the REST Mediator service.",
        )

        main_frame = ttk.Frame(self.menu_frame, padding=30)
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure((0, 1, 2), weight=1, uniform="menu")
        main_frame.rowconfigure(0, weight=1)

        # 3-Column Layout: Bahmni, O3, Mediator Side-by-Side
        self.create_tile(
            main_frame,
            "OpenMRS (Bahmni)",
            "Original workflow. Export, transform using imported mapping files, import, or run tracker sync.",
            "#0f766e",
            self.show_bahmni_page,
            row=0,
            col=0,
        )

        self.create_tile(
            main_frame,
            "OpenMRS 3 (O3)",
            "OpenMRS 3 workflow. Export O3 data, generate mapping files, transform, import, or run O3 sync.",
            "#0d9488",
            self.show_o3_page,
            row=0,
            col=1,
        )

        self.create_tile(
            main_frame,
            "🌐 REST Mediator Service",
            "REST Mediator microservice. Launch local REST API server, open Swagger docs, check sync status & resume sync.",
            "#1e40af",
            self.show_mediator_page,
            row=0,
            col=2,
        )

    def show_bahmni_page(self):
        self.clear_page()
        self.hide_menu()
        self.current_page = BahmniPage(self.root, self.show_source_menu)
        self.current_page.pack(fill="both", expand=True)

    def show_mediator_page(self):
        self.clear_page()
        self.hide_menu()
        self.current_page = MediatorPage(self.root, self.show_source_menu)
        self.current_page.pack(fill="both", expand=True)

    def show_bahmni_menu(self):
        self.clear_page()
        self.hide_menu()

        self.menu_frame = ttk.Frame(self.root)
        self.menu_frame.pack(fill="both", expand=True)

        self._build_header(
            self.menu_frame,
            "OpenMRS (Bahmni) Workflow",
            "Export, transform, import, or run the full tracker sync. These use the "
            "imported mapping files and dictionary that you select.",
        )

        top_bar = ttk.Frame(self.menu_frame, padding=(22, 8))
        top_bar.pack(fill="x")
        back_btn = ttk.Button(top_bar, text="\u2190 Back to Source Selection", command=self.show_source_menu)
        back_btn.pack(side="left")

        main_frame = ttk.Frame(self.menu_frame, padding=40)
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure((0, 1), weight=1, uniform="menu")
        main_frame.rowconfigure((0, 1, 2), weight=1, uniform="menu")

        items = [
            ("\U0001f4e4 EMR Data Export", "Fetch OpenMRS patient data by visit type and date.", "#0f766e", self.show_export),
            ("\U0001f504 Transform CSV", "Convert an OpenMRS export CSV into DHIS2 tracker CSV.\nSelect mapping Excel file.", "#2563eb", self.show_transform),
            ("\U0001f4e5 Import to DHIS2", "Import a transformed tracker CSV into DHIS2.", "#7c3aed", self.show_import),
            (
                "\u26a1 EMR-DHIS2 Tracker Sync",
                "Fetch, transform using mapping files, and import directly without creating an export file.",
                "#dc2626",
                self.show_sync,
            ),
        ]
        for index, (title, subtitle, color, command) in enumerate(items):
            row, column = divmod(index, 2)
            self.create_tile(main_frame, title, subtitle, color, command, row, column)

        spacer = ttk.Frame(main_frame)
        spacer.grid(row=2, column=1, sticky="nsew")

    def show_o3_page(self):
        self.clear_page()
        self.hide_menu()
        self.current_page = O3Page(self.root, self.show_source_menu)
        self.current_page.pack(fill="both", expand=True)

    def show_export(self):
        self.clear_page()
        self.hide_menu()
        self.current_page = ExportPage(self.root, self.show_bahmni_menu)
        self.current_page.pack(fill="both", expand=True)

    def show_transform(self):
        self.clear_page()
        self.hide_menu()
        self.current_page = TransformPage(self.root, self.show_bahmni_menu)
        self.current_page.pack(fill="both", expand=True)

    def show_import(self):
        self.clear_page()
        self.hide_menu()
        self.current_page = ImportPage(self.root, self.show_bahmni_menu)
        self.current_page.pack(fill="both", expand=True)

    def show_sync(self):
        self.clear_page()
        self.hide_menu()
        self.current_page = SyncPage(self.root, self.show_bahmni_menu)
        self.current_page.pack(fill="both", expand=True)


def main():
    root = tk.Tk()
    app = MainApplication(root)
    root.mainloop()


if __name__ == "__main__":
    main()
