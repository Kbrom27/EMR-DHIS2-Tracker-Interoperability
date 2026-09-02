"""
EMR-DHIS2 Interoperability Mediator GUI Management Page
"""

import json
import threading
import time
import urllib.request
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Optional

import uvicorn
from ui.components import LogPanel


class MediatorPage(ttk.Frame):
    def __init__(self, parent: tk.Misc, on_back: Callable[[], None]) -> None:
        super().__init__(parent, padding=16)
        self.on_back = on_back
        self.server_thread: Optional[threading.Thread] = None
        self.uvicorn_server: Optional[uvicorn.Server] = None
        self.is_running = False

        self._build_ui()
        self.check_server_status()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Header Frame
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(1, weight=1)

        ttk.Button(header, text="← Back to Main Menu", command=self.on_back).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="EMR-DHIS2 REST Mediator Service",
            font=("Segoe UI", 16, "bold"),
            foreground="#14324a",
        ).grid(row=0, column=1, padx=16, sticky="w")

        # Status & Control Card
        card = ttk.LabelFrame(self, text="Mediator Service Controls", padding=14)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        card.columnconfigure((0, 1, 2, 3), weight=1)

        self.status_label = ttk.Label(
            card,
            text="Status: Checking...",
            font=("Segoe UI", 11, "bold"),
            foreground="#64748b",
        )
        self.status_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.start_btn = ttk.Button(
            card, text="▶ Start Mediator Service", command=self.start_server
        )
        self.start_btn.grid(row=1, column=0, sticky="ew", padx=4, pady=4)

        self.stop_btn = ttk.Button(
            card, text="⏹ Stop Service", command=self.stop_server, state="disabled"
        )
        self.stop_btn.grid(row=1, column=1, sticky="ew", padx=4, pady=4)

        self.docs_btn = ttk.Button(
            card, text="🌐 Open API Docs (Swagger)", command=self.open_docs
        )
        self.docs_btn.grid(row=1, column=2, sticky="ew", padx=4, pady=4)

        self.status_btn = ttk.Button(
            card, text="🔄 Check Live Sync Status", command=self.fetch_sync_status
        )
        self.status_btn.grid(row=1, column=3, sticky="ew", padx=4, pady=4)

        # Checkpoint / Sync Info Frame
        info_frame = ttk.LabelFrame(self, text="Sync Checkpoint & Resumption", padding=12)
        info_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        info_frame.columnconfigure(0, weight=1)

        self.checkpoint_text = tk.StringVar(value="No sync checkpoint recorded yet.")
        ttk.Label(info_frame, textvariable=self.checkpoint_text, font=("Segoe UI", 10)).grid(
            row=0, column=0, sticky="w", pady=4
        )

        self.resume_btn = ttk.Button(
            info_frame, text="⚡ Resume Interrupted Sync", command=self.resume_sync, state="disabled"
        )
        self.resume_btn.grid(row=1, column=0, sticky="w", pady=6)

        # Log Panel
        self.log_panel = LogPanel(self, "Mediator Logs & Server Output")
        self.log_panel.grid(row=3, column=0, sticky="nsew")

    def check_server_status(self) -> None:
        def query():
            try:
                req = urllib.request.Request("http://127.0.0.1:8000/health", headers={"User-Agent": "MediatorGUI"})
                with urllib.request.urlopen(req, timeout=2) as res:
                    if res.status == 200:
                        self.root_update_status(True)
                        return
            except Exception:
                pass
            self.root_update_status(False)

        threading.Thread(target=query, daemon=True).start()

    def root_update_status(self, is_online: bool) -> None:
        self.is_running = is_online
        if is_online:
            self.status_label.config(text="Status: 🟢 Mediator Running on http://127.0.0.1:8000", foreground="#0f5132")
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.docs_btn.config(state="normal")
        else:
            self.status_label.config(text="Status: 🔴 Mediator Stopped", foreground="#842029")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")

        self.fetch_sync_status_quiet()

    def start_server(self) -> None:
        self.log_panel.write("Starting EMR-DHIS2 Mediator REST Service on http://127.0.0.1:8000 ...")
        
        def run():
            try:
                from mediator import app as mediator_app
                config = uvicorn.Config(mediator_app, host="127.0.0.1", port=8000, log_level="info")
                self.uvicorn_server = uvicorn.Server(config)
                self.uvicorn_server.run()
            except Exception as e:
                self.log_panel.write(f"Server Error: {e}")

        self.server_thread = threading.Thread(target=run, daemon=True)
        self.server_thread.start()

        time.sleep(1.5)
        self.check_server_status()

    def stop_server(self) -> None:
        if self.uvicorn_server:
            self.log_panel.write("Stopping Mediator Service...")
            self.uvicorn_server.should_exit = True
            self.uvicorn_server.force_exit = True

        def stop_worker():
            time.sleep(0.5)
            self.check_server_status()

        threading.Thread(target=stop_worker, daemon=True).start()

    def open_docs(self) -> None:
        webbrowser.open("http://127.0.0.1:8000/docs")

    def fetch_sync_status(self) -> None:
        self.log_panel.write("Fetching live sync status...")
        self.fetch_sync_status_quiet(verbose=True)

    def fetch_sync_status_quiet(self, verbose: bool = False) -> None:
        def query():
            chk = None
            failed_cnt = 0
            detailed_issues = []

            # 1. Try HTTP endpoint if server is running
            try:
                req = urllib.request.Request("http://127.0.0.1:8000/api/v1/sync/status", headers={"User-Agent": "MediatorGUI"})
                with urllib.request.urlopen(req, timeout=2) as res:
                    if res.status == 200:
                        data = json.loads(res.read().decode("utf-8"))
                        chk = data.get("checkpoint", {})
                        failed_cnt = data.get("failed_records_count", 0)
                        detailed_issues = data.get("failed_records_detail", [])
            except Exception:
                pass

            # 2. Fallback to local disk checkpoint if server is offline
            if not chk and Path("sync_checkpoint.json").is_file():
                try:
                    chk = json.loads(Path("sync_checkpoint.json").read_text(encoding="utf-8"))
                    dhis2_stats = chk.get("dhis2_import_stats", {})
                    failed_cnt = dhis2_stats.get("row_errors", 0)
                except Exception:
                    pass

            if chk and chk.get("status") != "idle":
                status = chk.get("status", "unknown")
                prog = chk.get("program", "N/A")
                fac = chk.get("facility_code", "N/A")

                summary = f"Program: {prog} | Facility: {fac} | Status: {status.upper()} | Failures: {failed_cnt}"
                self.checkpoint_text.set(summary)
                if chk.get("can_resume", False):
                    self.resume_btn.config(state="normal")
                else:
                    self.resume_btn.config(state="disabled")

                if verbose:
                    self.log_panel.write(f"Sync Checkpoint: {json.dumps(chk, indent=2)}")
                    if detailed_issues:
                        self.log_panel.write(f"Failed Records Detail: {json.dumps(detailed_issues, indent=2)}")
            else:
                self.checkpoint_text.set("No sync checkpoint recorded yet.")
                self.resume_btn.config(state="disabled")

        threading.Thread(target=query, daemon=True).start()

    def resume_sync(self) -> None:
        def query():
            try:
                self.log_panel.write("Sending resume request to Mediator...")
                req = urllib.request.Request(
                    "http://127.0.0.1:8000/api/v1/sync/resume",
                    data=json.dumps({}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "User-Agent": "MediatorGUI"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as res:
                    data = json.loads(res.read().decode("utf-8"))
                    self.log_panel.write(f"Sync Resume Result: {json.dumps(data, indent=2)}")
                    messagebox.showinfo("Sync Resumed", "Sync operation successfully resumed!")
                    self.fetch_sync_status_quiet()
            except Exception as e:
                self.log_panel.write(f"Resume Error: {e}")
                messagebox.showerror("Resume Error", str(e))

        threading.Thread(target=query, daemon=True).start()
