"""
cuepoint GUI — Tab-based layout with Scan, Results, and Settings tabs.
Run with: python -m cuepoint.gui
"""

import os
import tomllib

# Ensure tkinter can find Tcl/Tk when running from a venv on Windows
_tcl = r"C:\Program Files\Python313\tcl\tcl8.6"
_tk = r"C:\Program Files\Python313\tcl\tk8.6"
if os.path.isdir(_tcl):
    os.environ.setdefault("TCL_LIBRARY", _tcl)
    os.environ.setdefault("TK_LIBRARY", _tk)

import threading
import time
from datetime import datetime

import customtkinter as ctk
from loguru import logger
from tkcalendar import DateEntry

from . import db as store
from .enrichment import cleanup_cache
from .event_fetcher import CITIES, run_for_city_sync
from .fetch_following import fetch_following_slugs, update_following
from .generic import BASE_PATH

_SC_PROFILE_PATH = BASE_PATH / ".sc_profile"
_CONFIG_PATH = BASE_PATH / "config.toml"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Colors — neutral dark with teal accent
_BG_DARK = "#141414"
_BG_CARD = "#1e1e1e"
_BG_INPUT = "#2a2a2a"
_BG_BTN = "#333333"
_BG_BTN_HOVER = "#444444"
_ACCENT = "#50c8a8"
_ACCENT_HOVER = "#3daa8e"
_GREEN = "#50c8a8"
_RED = "#e06c75"
_ORANGE = "#e5a54b"
_TEXT = "#dcdcdc"
_TEXT_DIM = "#777777"


class TechnoScanApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("cuepoint")
        self.geometry("920x840")
        self.minsize(780, 680)
        self.resizable(True, True)
        self._cancel_requested = False
        self._running = False
        self._phase = ""
        self._run_start = 0.0
        self._current_city_idx = 0
        self._total_cities = 0
        self._city_results = []
        self._build_ui()

    # ================================================================== #
    #  UI construction                                                     #
    # ================================================================== #

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tabview = ctk.CTkTabview(
            self,
            fg_color=_BG_DARK,
            segmented_button_fg_color=_BG_CARD,
            segmented_button_selected_color=_ACCENT,
            segmented_button_selected_hover_color=_ACCENT_HOVER,
            segmented_button_unselected_color=_BG_BTN,
            segmented_button_unselected_hover_color=_BG_BTN_HOVER,
        )
        self.tabview.grid(row=0, column=0, padx=12, pady=12, sticky="nsew")

        self.tabview.add("Scan")
        self.tabview.add("Results")
        self.tabview.add("Settings")

        self._build_scan_tab()
        self._build_results_tab()
        self._build_settings_tab()

    # ------------------------------------------------------------------ #
    #  Scan tab                                                            #
    # ------------------------------------------------------------------ #

    def _build_scan_tab(self):
        tab = self.tabview.tab("Scan")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(5, weight=1)

        self._build_sc_section(tab)
        self._build_cities_section(tab)
        self._build_date_section(tab)
        self._build_controls(tab)
        self._build_progress_section(tab)
        self._build_log_section(tab)

    def _build_sc_section(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=_BG_CARD, corner_radius=10)
        frame.grid(row=0, column=0, padx=8, pady=(8, 6), sticky="ew")
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame, text="SoundCloud Profile", font=ctk.CTkFont(size=13, weight="bold"), text_color=_ACCENT
        ).grid(row=0, column=0, columnspan=4, padx=14, pady=(12, 2), sticky="w")

        ctk.CTkLabel(frame, text="URL:", text_color=_TEXT_DIM).grid(row=1, column=0, padx=(14, 6), pady=8, sticky="w")

        self.sc_url_entry = ctk.CTkEntry(
            frame,
            placeholder_text="https://soundcloud.com/your-username",
            fg_color=_BG_INPUT,
            border_color=_BG_INPUT,
            corner_radius=8,
            height=36,
        )
        self.sc_url_entry.grid(row=1, column=1, padx=4, pady=8, sticky="ew")

        self.save_sc_btn = ctk.CTkButton(
            frame,
            text="Save",
            width=60,
            height=34,
            corner_radius=8,
            fg_color=_BG_BTN,
            hover_color=_BG_BTN_HOVER,
            command=self._save_sc_profile,
        )
        self.save_sc_btn.grid(row=1, column=2, padx=(6, 2), pady=8)

        self.sync_btn = ctk.CTkButton(
            frame,
            text="Sync Following",
            width=140,
            height=34,
            corner_radius=8,
            fg_color=_BG_BTN,
            hover_color=_BG_BTN_HOVER,
            command=self._sync_following,
        )
        self.sync_btn.grid(row=1, column=3, padx=(2, 14), pady=8)

        self.sync_status = ctk.CTkLabel(frame, text="", text_color=_TEXT_DIM, font=ctk.CTkFont(size=11))
        self.sync_status.grid(row=2, column=0, columnspan=4, padx=14, pady=(0, 10), sticky="w")

        self._load_sc_profile()

    def _build_cities_section(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=_BG_CARD, corner_radius=10)
        frame.grid(row=1, column=0, padx=8, pady=6, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, padx=14, pady=(12, 6), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Cities", font=ctk.CTkFont(size=13, weight="bold"), text_color=_ACCENT).grid(
            row=0, column=0, sticky="w"
        )

        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(
            btn_frame,
            text="All",
            width=40,
            height=22,
            font=ctk.CTkFont(size=10),
            corner_radius=6,
            fg_color=_BG_BTN,
            hover_color=_BG_BTN_HOVER,
            command=lambda: self._set_all_cities(True),
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            btn_frame,
            text="None",
            width=40,
            height=22,
            font=ctk.CTkFont(size=10),
            corner_radius=6,
            fg_color=_BG_BTN,
            hover_color=_BG_BTN_HOVER,
            command=lambda: self._set_all_cities(False),
        ).pack(side="left", padx=2)

        self._city_container = ctk.CTkFrame(frame, fg_color="transparent")
        self._city_container.grid(row=1, column=0, padx=10, pady=(2, 10), sticky="ew")

        self.city_vars = {}
        self._city_cbs = []
        cities = sorted(CITIES.keys())
        for city in cities:
            var = ctk.BooleanVar(value=False)
            self.city_vars[city] = var
            cb = ctk.CTkCheckBox(
                self._city_container,
                text=city.capitalize(),
                variable=var,
                font=ctk.CTkFont(size=12),
                checkbox_width=20,
                checkbox_height=20,
                corner_radius=5,
                fg_color=_ACCENT,
                hover_color=_ACCENT_HOVER,
            )
            self._city_cbs.append(cb)

        self._city_cols = 0
        self._city_container.bind("<Configure>", self._reflow_cities)

    def _reflow_cities(self, event=None):
        w = self._city_container.winfo_width()
        if w <= 1:
            return
        cols = max(2, w // 160)
        if cols == self._city_cols:
            return
        self._city_cols = cols
        for cb in self._city_cbs:
            cb.grid_forget()
        for i, cb in enumerate(self._city_cbs):
            cb.grid(row=i // cols, column=i % cols, padx=8, pady=3, sticky="w")
        for c in range(cols):
            self._city_container.grid_columnconfigure(c, weight=1)

    def _build_date_section(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=_BG_CARD, corner_radius=10)
        frame.grid(row=2, column=0, padx=8, pady=6, sticky="ew")

        ctk.CTkLabel(frame, text="Date Range", font=ctk.CTkFont(size=13, weight="bold"), text_color=_ACCENT).grid(
            row=0, column=0, columnspan=5, padx=14, pady=(12, 6), sticky="w"
        )

        ctk.CTkLabel(frame, text="Start:", text_color=_TEXT_DIM).grid(row=1, column=0, padx=(14, 6), pady=8, sticky="w")

        self.start_date = DateEntry(
            frame,
            width=13,
            date_pattern="yyyy-mm-dd",
            background=_BG_INPUT,
            foreground=_TEXT,
            fieldbackground=_BG_INPUT,
            fieldforeground=_TEXT,
            selectbackground=_ACCENT,
            selectforeground="white",
            borderwidth=0,
            font=("Segoe UI", 11),
        )
        self.start_date.grid(row=1, column=1, padx=4, pady=8, sticky="w")

        ctk.CTkLabel(frame, text="Days:", text_color=_TEXT_DIM).grid(row=1, column=2, padx=(20, 6), pady=8, sticky="w")

        self.days_var = ctk.StringVar(value="7")
        self.days_entry = ctk.CTkEntry(
            frame,
            textvariable=self.days_var,
            width=60,
            justify="center",
            height=34,
            corner_radius=8,
            fg_color=_BG_INPUT,
            border_color=_BG_INPUT,
        )
        self.days_entry.grid(row=1, column=3, padx=4, pady=8, sticky="w")

        preset_frame = ctk.CTkFrame(frame, fg_color="transparent")
        preset_frame.grid(row=2, column=0, columnspan=5, padx=14, pady=(0, 12), sticky="w")

        ctk.CTkLabel(preset_frame, text="Presets:", text_color=_TEXT_DIM, font=ctk.CTkFont(size=11)).pack(
            side="left", padx=(0, 8)
        )

        for label, days in [("1 weekend", 3), ("2 weekends", 10), ("3 weekends", 17), ("1 month", 30)]:
            ctk.CTkButton(
                preset_frame,
                text=label,
                width=95,
                height=30,
                font=ctk.CTkFont(size=11),
                corner_radius=6,
                fg_color=_BG_BTN,
                hover_color=_BG_BTN_HOVER,
                command=lambda d=days: self.days_var.set(str(d)),
            ).pack(side="left", padx=3)

    def _build_controls(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=3, column=0, padx=8, pady=6, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)

        self.run_btn = ctk.CTkButton(
            frame,
            text="Run Scan",
            height=48,
            font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=10,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            command=self._run,
        )
        self.run_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.cancel_btn = ctk.CTkButton(
            frame,
            text="Cancel",
            height=48,
            width=100,
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=10,
            fg_color=_RED,
            hover_color="#c0555e",
            state="disabled",
            command=self._cancel,
        )
        self.cancel_btn.grid(row=0, column=1, sticky="e")

    def _build_progress_section(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=_BG_CARD, corner_radius=10)
        frame.grid(row=4, column=0, padx=8, pady=(4, 0), sticky="ew")
        frame.grid_columnconfigure(0, weight=1)

        info_frame = ctk.CTkFrame(frame, fg_color="transparent")
        info_frame.grid(row=0, column=0, padx=14, pady=(10, 4), sticky="ew")
        info_frame.grid_columnconfigure(0, weight=1)

        self.phase_label = ctk.CTkLabel(
            info_frame, text="Ready", font=ctk.CTkFont(size=12, weight="bold"), text_color=_TEXT_DIM
        )
        self.phase_label.grid(row=0, column=0, sticky="w")

        self.time_label = ctk.CTkLabel(info_frame, text="", font=ctk.CTkFont(size=11), text_color=_TEXT_DIM)
        self.time_label.grid(row=0, column=1, sticky="e")

        self.progress_bar = ctk.CTkProgressBar(
            frame, height=10, corner_radius=5, fg_color=_BG_INPUT, progress_color=_ACCENT
        )
        self.progress_bar.grid(row=1, column=0, padx=14, pady=(0, 4), sticky="ew")
        self.progress_bar.set(0)

        self.city_label = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=11), text_color=_TEXT_DIM)
        self.city_label.grid(row=2, column=0, padx=14, pady=(0, 10), sticky="w")

    def _build_log_section(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=_BG_CARD, corner_radius=10)
        frame.grid(row=5, column=0, padx=8, pady=(4, 8), sticky="nsew")
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, padx=14, pady=(10, 4), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Log", font=ctk.CTkFont(size=13, weight="bold"), text_color=_ACCENT).grid(
            row=0, column=0, sticky="w"
        )

        ctk.CTkButton(
            header,
            text="Clear",
            width=60,
            height=24,
            font=ctk.CTkFont(size=11),
            corner_radius=6,
            fg_color=_BG_BTN,
            hover_color=_BG_BTN_HOVER,
            command=self._clear_log,
        ).grid(row=0, column=1, sticky="e")

        self.log_box = ctk.CTkTextbox(
            frame,
            state="disabled",
            wrap="word",
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=_BG_DARK,
            corner_radius=8,
            text_color=_TEXT,
        )
        self.log_box.grid(row=1, column=0, padx=14, pady=(0, 14), sticky="nsew")

    # ------------------------------------------------------------------ #
    #  Results tab                                                         #
    # ------------------------------------------------------------------ #

    def _build_results_tab(self):
        tab = self.tabview.tab("Results")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        # Header with Open All button
        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.grid(row=0, column=0, padx=8, pady=(8, 4), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Scan Results", font=ctk.CTkFont(size=15, weight="bold"), text_color=_ACCENT).grid(
            row=0, column=0, sticky="w"
        )

        self.open_all_btn = ctk.CTkButton(
            header,
            text="Open All Reports",
            width=150,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            command=self._open_all_reports,
        )
        self.open_all_btn.grid(row=0, column=1, sticky="e")

        # Scrollable results list
        self.results_scroll = ctk.CTkScrollableFrame(tab, fg_color=_BG_DARK, corner_radius=10)
        self.results_scroll.grid(row=1, column=0, padx=8, pady=(4, 8), sticky="nsew")
        self.results_scroll.grid_columnconfigure(0, weight=1)

        self.no_results_label = ctk.CTkLabel(
            self.results_scroll,
            text="No scan results yet.\nRun a scan to see results here.",
            font=ctk.CTkFont(size=13),
            text_color=_TEXT_DIM,
        )
        self.no_results_label.grid(row=0, column=0, pady=40)

    def _add_result_card(self, result):
        """Add a city result card to the Results tab."""
        self._city_results.append(result)
        idx = len(self._city_results) - 1

        # Hide "no results" placeholder
        self.no_results_label.grid_forget()

        error = result.get("error")
        border_color = _RED if error else _ACCENT

        card = ctk.CTkFrame(
            self.results_scroll,
            fg_color=_BG_CARD,
            corner_radius=10,
            border_width=1,
            border_color=border_color,
        )
        card.grid(row=idx, column=0, padx=4, pady=5, sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        # Status dot + city name
        dot_color = _RED if error else _GREEN
        ctk.CTkLabel(card, text="●", font=ctk.CTkFont(size=10), text_color=dot_color, width=16).grid(
            row=0, column=0, padx=(12, 0), pady=(12, 10), sticky="w"
        )
        ctk.CTkLabel(
            card, text=result.get("city", "?"), font=ctk.CTkFont(size=14, weight="bold"), text_color=_TEXT
        ).grid(row=0, column=0, padx=(28, 8), pady=(12, 10), sticky="w")

        # Stats
        events = result.get("events", 0)
        followed = result.get("followed", 0)

        if error:
            stat_text = f"Error: {error}"
            stat_color = _RED
        else:
            parts = [f"{events} events"]
            if followed:
                parts.append(f"{followed} followed")
            stat_text = " \u00b7 ".join(parts)
            stat_color = _TEXT_DIM

        ctk.CTkLabel(card, text=stat_text, font=ctk.CTkFont(size=12), text_color=stat_color).grid(
            row=0, column=1, padx=4, pady=(12, 10), sticky="w"
        )

        # Open Report button
        file_path = result.get("file_path")
        if file_path and os.path.exists(file_path):
            ctk.CTkButton(
                card,
                text="Open Report",
                width=110,
                height=32,
                font=ctk.CTkFont(size=11, weight="bold"),
                corner_radius=8,
                fg_color=_ACCENT,
                hover_color=_ACCENT_HOVER,
                command=lambda p=file_path: os.startfile(p),
            ).grid(row=0, column=2, padx=(4, 14), pady=(12, 10))

    def _open_all_reports(self):
        for result in self._city_results:
            fp = result.get("file_path")
            if fp and os.path.exists(fp):
                os.startfile(fp)

    def _clear_results(self):
        """Clear all result cards."""
        self._city_results.clear()
        for widget in self.results_scroll.winfo_children():
            widget.destroy()
        self.no_results_label = ctk.CTkLabel(
            self.results_scroll,
            text="No scan results yet.\nRun a scan to see results here.",
            font=ctk.CTkFont(size=13),
            text_color=_TEXT_DIM,
        )
        self.no_results_label.grid(row=0, column=0, pady=40)

    # ------------------------------------------------------------------ #
    #  Settings tab                                                        #
    # ------------------------------------------------------------------ #

    def _build_settings_tab(self):
        tab = self.tabview.tab("Settings")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        # Load current config
        self._settings_vars = {}
        cfg = {}
        try:
            with open(_CONFIG_PATH, "rb") as f:
                cfg = tomllib.load(f)
        except FileNotFoundError:
            pass

        # --- Genre filter ---
        genre_frame = ctk.CTkFrame(tab, fg_color=_BG_CARD, corner_radius=10)
        genre_frame.grid(row=0, column=0, padx=8, pady=(8, 4), sticky="ew")
        genre_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            genre_frame, text="Genre Filter", font=ctk.CTkFont(size=13, weight="bold"), text_color=_ACCENT
        ).grid(row=0, column=0, padx=14, pady=(12, 4), sticky="w")

        ctk.CTkLabel(
            genre_frame,
            text="Comma-separated list of genres to include:",
            font=ctk.CTkFont(size=11),
            text_color=_TEXT_DIM,
        ).grid(row=1, column=0, padx=14, pady=(0, 4), sticky="w")

        genres = cfg.get("genres", {}).get("filter", ["Techno", "Drum & Bass", "Drum n Bass"])
        self.genre_entry = ctk.CTkEntry(
            genre_frame, height=36, corner_radius=8, fg_color=_BG_INPUT, border_color=_BG_INPUT
        )
        self.genre_entry.grid(row=2, column=0, padx=14, pady=(0, 12), sticky="ew")
        self.genre_entry.insert(0, ", ".join(genres))

        # --- Scoring weights ---
        scoring_frame = ctk.CTkFrame(tab, fg_color=_BG_CARD, corner_radius=10)
        scoring_frame.grid(row=1, column=0, padx=8, pady=6, sticky="ew")

        ctk.CTkLabel(
            scoring_frame, text="Scoring Weights", font=ctk.CTkFont(size=13, weight="bold"), text_color=_ACCENT
        ).grid(row=0, column=0, columnspan=6, padx=14, pady=(12, 6), sticky="w")

        scoring_fields = [
            ("SC weight", "scoring", "sc_weight", 10),
            ("DC weight", "scoring", "dc_weight", 5),
            ("BC weight", "scoring", "bc_weight", 8),
            ("RA genre bonus", "scoring", "ra_genre_bonus", 5000),
            ("Followed bonus", "scoring", "followed_bonus", 1000000),
        ]
        for i, (label, section, key, default) in enumerate(scoring_fields):
            val = cfg.get(section, {}).get(key, default)
            var = ctk.StringVar(value=str(val))
            self._settings_vars[(section, key)] = var

            ctk.CTkLabel(scoring_frame, text=label + ":", text_color=_TEXT_DIM, font=ctk.CTkFont(size=11)).grid(
                row=1 + i // 3, column=(i % 3) * 2, padx=(14, 4), pady=4, sticky="w"
            )
            ctk.CTkEntry(
                scoring_frame,
                textvariable=var,
                width=120,
                height=32,
                corner_radius=6,
                fg_color=_BG_INPUT,
                border_color=_BG_INPUT,
                justify="center",
            ).grid(row=1 + i // 3, column=(i % 3) * 2 + 1, padx=(0, 14), pady=6, sticky="w")

        # --- Cache & threshold settings ---
        cache_frame = ctk.CTkFrame(tab, fg_color=_BG_CARD, corner_radius=10)
        cache_frame.grid(row=2, column=0, padx=8, pady=6, sticky="ew")

        ctk.CTkLabel(
            cache_frame, text="Cache & Thresholds", font=ctk.CTkFont(size=13, weight="bold"), text_color=_ACCENT
        ).grid(row=0, column=0, columnspan=6, padx=14, pady=(12, 6), sticky="w")

        cache_fields = [
            ("Cache TTL (days)", "cache", "ttl_days", 30),
            ("Following TTL", "cache", "ttl_following_days", 7),
            ("Stale (days)", "cache", "stale_days", 14),
            ("SC threshold", "scoring", "lineup_sc_threshold", 1000),
            ("DC threshold", "scoring", "lineup_dc_threshold", 50),
            ("BC threshold", "scoring", "lineup_bc_threshold", 30),
        ]
        for i, (label, section, key, default) in enumerate(cache_fields):
            val = cfg.get(section, {}).get(key, default)
            var = ctk.StringVar(value=str(val))
            self._settings_vars[(section, key)] = var

            ctk.CTkLabel(cache_frame, text=label + ":", text_color=_TEXT_DIM, font=ctk.CTkFont(size=11)).grid(
                row=1 + i // 3, column=(i % 3) * 2, padx=(14, 4), pady=4, sticky="w"
            )
            ctk.CTkEntry(
                cache_frame,
                textvariable=var,
                width=100,
                height=32,
                corner_radius=6,
                fg_color=_BG_INPUT,
                border_color=_BG_INPUT,
                justify="center",
            ).grid(row=1 + i // 3, column=(i % 3) * 2 + 1, padx=(0, 14), pady=6, sticky="w")

        # Padding row for spacing
        pad = ctk.CTkFrame(cache_frame, fg_color="transparent", height=8)
        pad.grid(row=10, column=0, columnspan=6)

        # --- Save button ---
        save_frame = ctk.CTkFrame(tab, fg_color="transparent")
        save_frame.grid(row=3, column=0, padx=8, pady=(8, 8), sticky="ew")
        save_frame.grid_columnconfigure(0, weight=1)

        self.settings_status = ctk.CTkLabel(save_frame, text="", font=ctk.CTkFont(size=11), text_color=_TEXT_DIM)
        self.settings_status.grid(row=0, column=0, sticky="w", padx=14)

        ctk.CTkButton(
            save_frame,
            text="Save Settings",
            width=140,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8,
            fg_color=_ACCENT,
            hover_color=_ACCENT_HOVER,
            command=self._save_settings,
        ).grid(row=0, column=1, sticky="e", padx=8)

    def _save_settings(self):
        """Write current settings to config.toml."""
        try:
            # Read existing config to preserve cities and other sections
            try:
                with open(_CONFIG_PATH, "rb") as f:
                    cfg = tomllib.load(f)
            except FileNotFoundError:
                cfg = {}

            # Update from GUI vars
            for (section, key), var in self._settings_vars.items():
                if section not in cfg:
                    cfg[section] = {}
                try:
                    cfg[section][key] = int(var.get())
                except ValueError:
                    try:
                        cfg[section][key] = float(var.get())
                    except ValueError:
                        cfg[section][key] = var.get()

            # Update genres
            genre_text = self.genre_entry.get().strip()
            if genre_text:
                cfg.setdefault("genres", {})["filter"] = [g.strip() for g in genre_text.split(",") if g.strip()]

            # Write TOML manually (tomllib is read-only)
            lines = []
            for section, values in cfg.items():
                if isinstance(values, dict) and all(isinstance(v, dict) for v in values.values()):
                    # Nested section (like [cities.berlin])
                    for sub_key, sub_val in values.items():
                        lines.append(f"[{section}.{sub_key}]")
                        for k, v in sub_val.items():
                            lines.append(f"{k} = {_toml_value(v)}")
                        lines.append("")
                else:
                    lines.append(f"[{section}]")
                    for k, v in values.items():
                        lines.append(f"{k} = {_toml_value(v)}")
                    lines.append("")

            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

            # Reload config module
            from . import config as cfg_mod

            cfg_mod._cfg = None
            cfg_mod._load()

            self.settings_status.configure(text="Settings saved!", text_color=_GREEN)
        except Exception as e:
            self.settings_status.configure(text=f"Error: {e}", text_color=_RED)

    # ================================================================== #
    #  Progress tracking                                                   #
    # ================================================================== #

    def _handle_progress(self, data):
        """Callback from event_fetcher's progress_cb."""
        phase = data.get("phase", "")
        detail = data.get("detail", "")
        pct = data.get("pct", 0)

        # Scale per-city progress into overall progress
        if self._total_cities:
            city_weight = 1.0 / self._total_cities
            overall_pct = self._current_city_idx * city_weight + pct * city_weight
        else:
            overall_pct = pct

        # Map phase names to readable labels
        phase_labels = {
            "fetch_ra": "Fetching RA events...",
            "enrich": "Enriching artists...",
            "enrich_sc": "SoundCloud enrichment",
            "enrich_discogs": "Discogs enrichment",
            "enrich_bandcamp": "Bandcamp enrichment",
            "saving": "Saving to cache...",
            "clubs": "Scraping clubs...",
            "filter": "Filtering & scoring...",
            "report": "Generating report...",
            "done": "Complete!",
        }
        label = phase_labels.get(phase, phase)
        if detail:
            label = f"{label} — {detail}"

        self.after(0, lambda: self.phase_label.configure(text=label, text_color=_TEXT))
        self.after(0, lambda: self.progress_bar.set(min(overall_pct, 0.99)))

    def _update_timer(self):
        if not self._running:
            return
        elapsed = time.monotonic() - self._run_start
        mins, secs = divmod(int(elapsed), 60)
        self.time_label.configure(text=f"{mins:02d}:{secs:02d} elapsed")
        self.after(1000, self._update_timer)

    def _set_city_progress(self, idx, total, city_name):
        self._current_city_idx = idx
        self._total_cities = total
        self.after(0, lambda: self.city_label.configure(text=f"City {idx + 1}/{total}: {city_name}"))

    # ================================================================== #
    #  Actions                                                             #
    # ================================================================== #

    def _log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _load_sc_profile(self):
        try:
            url = _SC_PROFILE_PATH.read_text(encoding="utf-8").strip()
            if url:
                self.sc_url_entry.insert(0, url)
        except FileNotFoundError:
            pass

    def _save_sc_profile(self):
        url = self.sc_url_entry.get().strip()
        if not url:
            self.sync_status.configure(text="Enter a SoundCloud profile URL first.", text_color=_ORANGE)
            return
        _SC_PROFILE_PATH.write_text(url, encoding="utf-8")
        self.sync_status.configure(text="Profile saved.", text_color=_GREEN)

    def _sync_following(self):
        url = self.sc_url_entry.get().strip()
        if not url:
            self.sync_status.configure(text="Enter a SoundCloud profile URL first.", text_color=_ORANGE)
            return

        self.sync_btn.configure(state="disabled", text="Syncing...")
        self.sync_status.configure(text="Fetching following list from SoundCloud...", text_color=_TEXT_DIM)

        def _do():
            try:
                self._log(f"Fetching following list for {url} ...")
                slugs = fetch_following_slugs(url)
                update_following(slugs)
                msg = f"Synced {len(slugs)} artists to following.txt"
                self.after(0, lambda: self.sync_status.configure(text=msg, text_color=_GREEN))
                self.after(0, lambda: self._log(f"Done: {msg}"))
            except Exception as e:
                err = str(e)
                self.after(0, lambda: self.sync_status.configure(text=f"Error: {err}", text_color=_RED))
                self.after(0, lambda: self._log(f"Sync error: {err}"))
            finally:
                self.after(0, lambda: self.sync_btn.configure(state="normal", text="Sync Following"))

        threading.Thread(target=_do, daemon=True).start()

    def _set_all_cities(self, value):
        for var in self.city_vars.values():
            var.set(value)

    def _cancel(self):
        if self._running:
            self._cancel_requested = True
            self.cancel_btn.configure(state="disabled", text="Cancelling...")
            self._log("Cancel requested... finishing current city.")

    def _run(self):
        selected = [c for c, v in self.city_vars.items() if v.get()]
        if not selected:
            self._log("Select at least one city.")
            return

        try:
            days = int(self.days_var.get())
            if days < 1:
                raise ValueError
        except ValueError:
            self._log("Days must be a positive number.")
            return

        start_str = self.start_date.get_date().strftime("%Y-%m-%d")
        start_date = datetime.strptime(start_str, "%Y-%m-%d")

        # Clear previous results
        self._clear_results()

        # UI state: running
        self._running = True
        self._cancel_requested = False
        self._run_start = time.monotonic()
        self.run_btn.configure(state="disabled", text="Running...", fg_color=_BG_BTN)
        self.cancel_btn.configure(state="normal", text="Cancel")
        self.phase_label.configure(text="Starting...", text_color=_TEXT)
        self.progress_bar.set(0)

        self._log(
            f"\n{'=' * 50}\n"
            f"  Cities : {', '.join(c.capitalize() for c in selected)}\n"
            f"  Start  : {start_str}  |  Days: {days}\n"
            f"{'=' * 50}"
        )

        self._update_timer()

        def _do():
            sink_id = logger.add(
                lambda msg: self.after(0, lambda m=msg: self._log(m.strip())),
                format="{time:HH:mm:ss} | {level:<7} | {message}",
                colorize=False,
            )
            try:
                store.migrate_if_needed()
                cleanup_cache()
                total = len(selected)
                for i, city in enumerate(selected):
                    if self._cancel_requested:
                        self.after(0, lambda: self._log("Cancelled by user."))
                        break
                    city_name = CITIES[city][1]
                    self.after(0, lambda idx=i, t=total, cn=city_name: self._set_city_progress(idx, t, cn))
                    result = run_for_city_sync(city, start_date, days, progress_cb=self._handle_progress)
                    if result:
                        self.after(0, lambda r=result: self._add_result_card(r))

                elapsed = time.monotonic() - self._run_start
                mins, secs = divmod(int(elapsed), 60)
                if self._cancel_requested:
                    final_msg = f"Cancelled after {mins}m {secs}s"
                    final_color = _ORANGE
                else:
                    final_msg = f"All done! {total} cities in {mins}m {secs}s"
                    final_color = _GREEN

                self.after(0, lambda: self.progress_bar.set(1.0))
                self.after(0, lambda: self.phase_label.configure(text=final_msg, text_color=final_color))
                self.after(0, lambda: self._log(f"\n{final_msg}"))
                self.after(0, lambda: self.city_label.configure(text=""))

                # Switch to Results tab when done
                self.after(0, lambda: self.tabview.set("Results"))

            except Exception as e:
                elapsed = time.monotonic() - self._run_start
                mins, secs = divmod(int(elapsed), 60)
                err_msg = str(e)
                self.after(0, lambda: self.phase_label.configure(text=f"Error: {err_msg}", text_color=_RED))
                self.after(0, lambda: self._log(f"\nERROR: {err_msg}  ({mins}m {secs}s)"))
            finally:
                logger.remove(sink_id)
                self._running = False
                self.after(0, lambda: self.run_btn.configure(state="normal", text="Run Scan", fg_color=_ACCENT))
                self.after(0, lambda: self.cancel_btn.configure(state="disabled", text="Cancel"))

        threading.Thread(target=_do, daemon=True).start()


def _toml_value(v):
    """Format a Python value as a TOML literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        items = ", ".join(_toml_value(x) for x in v)
        return f"[{items}]"
    return repr(v)


if __name__ == "__main__":
    app = TechnoScanApp()
    app.mainloop()
