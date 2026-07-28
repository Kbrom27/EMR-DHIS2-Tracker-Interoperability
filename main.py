#!/usr/bin/env python3
"""
EMR-DHIS2 Tracker Interoperability - Main Application
"""

import tkinter as tk
from tkinter import ttk

from ui.export_page import ExportPage
from ui.transform_page import TransformPage
from ui.import_page import ImportPage
from ui.sync_page import SyncPage


APP_TITLE = "EMR-DHIS2 Tracker interoperability"


class MainApplication:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x780")
        self.root.minsize(980, 680)

        self.current_page = None
        self.main_menu_frame = None
        self.configure_style()
        self.show_main_menu()

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

    def show_main_menu(self):
        self.clear_page()

        self.main_menu_frame = ttk.Frame(self.root)
        self.main_menu_frame.pack(fill="both", expand=True)

        header = ttk.Frame(self.main_menu_frame, style="Header.TFrame", padding=(22, 18))
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

        main_frame = ttk.Frame(self.main_menu_frame, padding=40)
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure((0, 1), weight=1, uniform="menu")
        main_frame.rowconfigure((0, 1), weight=1, uniform="menu")

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
            self.create_card(main_frame, title, subtitle, color, command, row, column)

    def create_card(self, parent, title, subtitle, color, command, row, col):
        tile = tk.Frame(parent, bg=color, padx=20, pady=18, cursor="hand2")
        tile.grid(row=row, column=col, sticky="nsew", padx=10, pady=10)
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
        tile.bind("<Button-1>", lambda e, cmd=command: cmd())

    def show_export(self):
        self.clear_page()
        if self.main_menu_frame:
            self.main_menu_frame.destroy()
            self.main_menu_frame = None
        self.current_page = ExportPage(self.root, self.show_main_menu)
        self.current_page.pack(fill="both", expand=True)

    def show_transform(self):
        self.clear_page()
        if self.main_menu_frame:
            self.main_menu_frame.destroy()
            self.main_menu_frame = None
        self.current_page = TransformPage(self.root, self.show_main_menu)
        self.current_page.pack(fill="both", expand=True)

    def show_import(self):
        self.clear_page()
        if self.main_menu_frame:
            self.main_menu_frame.destroy()
            self.main_menu_frame = None
        self.current_page = ImportPage(self.root, self.show_main_menu)
        self.current_page.pack(fill="both", expand=True)

    def show_sync(self):
        self.clear_page()
        if self.main_menu_frame:
            self.main_menu_frame.destroy()
            self.main_menu_frame = None
        self.current_page = SyncPage(self.root, self.show_main_menu)
        self.current_page.pack(fill="both", expand=True)


def main():
    root = tk.Tk()
    app = MainApplication(root)
    root.mainloop()


if __name__ == "__main__":
    main()
