"""
OpenMRS (Bahmni) Multi-Tab Unified Page
----------------------------------------
Presents all 4 Bahmni workflows (Export, Transform, Import, Sync) in a single
tabbed navigation interface, identical to the OpenMRS 3 (O3) workflow page.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List

from ui.export_page import ExportPage
from ui.transform_page import TransformPage
from ui.import_page import ImportPage
from ui.sync_page import SyncPage

NAV_ITEMS = [
    ("export", "📥 Export EMR Data"),
    ("transform", "🔄 Transform CSV"),
    ("import", "📤 Import to DHIS2"),
    ("sync", "⚡ Full Tracker Sync"),
]


class BahmniPage(ttk.Frame):
    def __init__(self, parent: tk.Misc, on_back_to_menu):
        super().__init__(parent)
        self.parent = parent
        self.on_back_to_menu = on_back_to_menu

        self._views: Dict[str, ttk.Frame] = {}
        self._nav_buttons: Dict[str, tk.Button] = {}

        self._build_ui()
        self._show_view("export")

    def _build_ui(self):
        # Top Header
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 14))
        header.pack(fill="x")
        
        back_btn = ttk.Button(header, text="← Back to Main Menu", command=self.go_back)
        back_btn.pack(side="left")

        ttk.Label(
            header,
            text="OpenMRS (Bahmni) Interoperability Workflow",
            style="Header.TLabel",
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="center")

        ttk.Label(
            header,
            text="Export EMR data, transform CSV using mapping files, import to DHIS2, or run full sync.",
            style="Header.TLabel",
            font=("Segoe UI", 10),
        ).pack(anchor="center", pady=(4, 0))

        # Top Navigation Bar (Tabs)
        nav_bar = tk.Frame(self, bg="#eef4f8", padx=6, pady=8)
        nav_bar.pack(fill="x")

        for key, label in NAV_ITEMS:
            btn = self._make_nav_button(nav_bar, label, lambda k=key: self._show_view(k))
            btn.pack(side="left", padx=4)
            self._nav_buttons[key] = btn

        # View Container
        self.container = ttk.Frame(self)
        self.container.pack(fill="both", expand=True)

        # Build embedded sub-pages (passing a dummy on_back since navigation is handled by tabs)
        noop_back = lambda: None
        self._views["export"] = ExportPage(self.container, noop_back)
        self._views["transform"] = TransformPage(self.container, noop_back)
        self._views["import"] = ImportPage(self.container, noop_back)
        self._views["sync"] = SyncPage(self.container, noop_back)

        # Hide the inner header of embedded pages so the top header/tabs remain unified
        for page in self._views.values():
            for child in page.winfo_children():
                if isinstance(child, ttk.Frame) and child.cget("style") == "Header.TFrame":
                    child.pack_forget()

    def _make_nav_button(self, parent, text, command):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg="#cbd5e1",
            fg="#1e293b",
            activebackground="#94a3b8",
            activeforeground="#ffffff",
            relief="flat",
            padx=16,
            pady=8,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )

    def _show_view(self, active_key: str):
        for key, view in self._views.items():
            if key == active_key:
                view.pack(fill="both", expand=True)
            else:
                view.pack_forget()

        for key, btn in self._nav_buttons.items():
            if key == active_key:
                btn.config(bg="#0f766e", fg="white")
            else:
                btn.config(bg="#cbd5e1", fg="#1e293b")

    def go_back(self):
        self.on_back_to_menu()
        self.destroy()
