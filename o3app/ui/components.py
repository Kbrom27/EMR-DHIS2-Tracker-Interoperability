from __future__ import annotations

import calendar
import tkinter as tk
from datetime import date, datetime
from tkinter import ttk
from typing import Optional, Sequence


class SearchableCombobox(ttk.Combobox):
    def __init__(self, parent: tk.Misc, values: Sequence[str] = (), **kwargs) -> None:
        self.all_values = list(values)
        if "state" not in kwargs or kwargs.get("state") == "readonly":
            kwargs["state"] = "normal"
        super().__init__(parent, values=self.all_values, **kwargs)
        self.bind("<KeyRelease>", self._on_key_release)
        self.bind("<FocusIn>", self._on_focus_in)

    def set_values(self, new_values: Sequence[str]) -> None:
        self.all_values = list(new_values)
        self["values"] = self.all_values

    def _on_key_release(self, event: tk.Event) -> None:
        if event.keysym in (
            "Up", "Down", "Left", "Right", "Return", "Escape", "Tab",
            "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R"
        ):
            return
        query = self.get().strip().lower()
        if not query:
            self["values"] = self.all_values
        else:
            filtered = [val for val in self.all_values if query in val.lower()]
            self["values"] = filtered if filtered else self.all_values

    def _on_focus_in(self, event: tk.Event) -> None:
        query = self.get().strip().lower()
        if not query:
            self["values"] = self.all_values
        else:
            filtered = [val for val in self.all_values if query in val.lower()]
            self["values"] = filtered if filtered else self.all_values


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
