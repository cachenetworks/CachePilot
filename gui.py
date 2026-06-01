"""
Modern dashboard UI for CachePilot using CustomTkinter.

Tabs:
  Dashboard — status cards, last command, recent activity, live mic VU.
  Commands  — browsable list, edit response variants & aliases inline.
  Crew      — department -> voice assignments.
  Voices    — voice catalogue with click-to-test buttons.
  Logs      — color-coded activity feed with search.
  Settings  — relay, force voice, fuzzy threshold, voice style.

AppState is preserved exactly as before so main.py needs no changes.
"""

import json
import os
import queue
import threading
import time

import customtkinter as ctk
import tkinter as tk

import numpy as np

try:
    import sounddevice as sd
    _HAS_SD = True
except Exception:
    _HAS_SD = False


# ---------------------------------------------------------------------------
# Visual identity
# ---------------------------------------------------------------------------

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")  # base; we override colors below

# Color palette: deep navy cockpit, cyan accent, amber warnings
COLOR_BG       = "#0a0e1a"
COLOR_PANEL    = "#121826"
COLOR_PANEL_HI = "#1a2236"
COLOR_BORDER   = "#1f2937"
COLOR_ACCENT   = "#00d4ff"
COLOR_ACCENT_DARK = "#0099bb"
COLOR_GOOD     = "#22c55e"
COLOR_WARN     = "#ffb800"
COLOR_BAD      = "#ef4444"
COLOR_TEXT     = "#e2e8f0"
COLOR_DIM      = "#64748b"
COLOR_MUTED_BG = "#3b0d12"

FONT_TITLE = ("Segoe UI", 16, "bold")
FONT_SUB   = ("Segoe UI", 11, "bold")
FONT_BODY  = ("Segoe UI", 11)
FONT_MONO  = ("Consolas", 10)


# ---------------------------------------------------------------------------
# Thread-safe state shared with the listen loop
# (interface preserved 1:1 from the previous Tkinter version)
# ---------------------------------------------------------------------------

class AppState:

    def __init__(self, profile="ship", voice="alan",
                 profiles=None, voices=None,
                 relay_enabled=True, copilot_voice="alan"):
        self._lock = threading.Lock()
        self._muted = False
        self._running = True
        self._reload_requested = False
        self._profile = profile
        self._voice = voice
        self.available_profiles = list(profiles or ["ship", "fps"])
        self.available_voices = list(voices or ["alan"])
        self._voice_change = None
        self._assignments = {}
        self._force_voice = None
        self._relay_enabled = bool(relay_enabled)
        self._copilot_voice = copilot_voice
        self.log_queue = queue.Queue()

    # mute
    def is_muted(self):
        with self._lock: return self._muted
    def set_muted(self, v):
        with self._lock: self._muted = bool(v)
    def toggle_muted(self):
        with self._lock:
            self._muted = not self._muted
            return self._muted

    # running
    def is_running(self):
        with self._lock: return self._running
    def request_stop(self):
        with self._lock: self._running = False

    # reload
    def request_reload(self):
        with self._lock: self._reload_requested = True
    def consume_reload(self):
        with self._lock:
            r = self._reload_requested
            self._reload_requested = False
            return r

    # profile
    def get_profile(self):
        with self._lock: return self._profile
    def set_profile(self, name):
        with self._lock: self._profile = name

    # voice (default)
    def get_voice(self):
        with self._lock: return self._voice
    def request_voice_change(self, name):
        with self._lock: self._voice_change = name
    def consume_voice_change(self):
        with self._lock:
            v = self._voice_change
            self._voice_change = None
            return v

    # assignments
    def get_assignments(self):
        with self._lock:
            return dict(self._assignments) if self._assignments else None
    def set_assignments(self, mapping):
        with self._lock: self._assignments = dict(mapping or {})
    def update_assignment(self, category, voice):
        with self._lock: self._assignments[category] = voice

    # force voice
    def get_force_voice(self):
        with self._lock: return self._force_voice
    def set_force_voice(self, voice):
        with self._lock: self._force_voice = voice or None

    # co-pilot relay
    def get_relay_enabled(self):
        with self._lock: return self._relay_enabled
    def set_relay_enabled(self, e):
        with self._lock: self._relay_enabled = bool(e)
    def get_copilot_voice(self):
        with self._lock: return self._copilot_voice
    def set_copilot_voice(self, v):
        with self._lock: self._copilot_voice = v or None

    # logging
    def log(self, message):
        self.log_queue.put(message)


# ---------------------------------------------------------------------------
# Mic level monitor (for VU bar)
# ---------------------------------------------------------------------------

class MicLevel:
    """Background-thread RMS sampler from the default mic, ~30 FPS."""

    def __init__(self):
        self.level = 0.0  # 0..1
        self._stop = threading.Event()
        if _HAS_SD:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self):
        try:
            with sd.InputStream(channels=1, samplerate=16000,
                                blocksize=512, dtype="float32") as stream:
                while not self._stop.is_set():
                    data, _ = stream.read(512)
                    if data.size:
                        rms = float(np.sqrt(np.mean(data ** 2)))
                        # Compress to 0..1 with a log-ish curve
                        v = min(1.0, rms * 6.0)
                        # Light smoothing for nicer animation
                        self.level = self.level * 0.6 + v * 0.4
        except Exception:
            pass

    def stop(self):
        self._stop.set()


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------

class CachePilotGUI:
    """Modern multi-tab dashboard built with CustomTkinter."""

    DEPARTMENT_LABELS = {
        "atc":                      "ATC / Tower",
        "ship_flight":              "Cockpit / Flight",
        "power":                    "Engineering / Power",
        "ship_weapons":             "Tactical / Weapons",
        "targeting":                "Tactical / Targeting",
        "shields_countermeasures":  "Tactical / Shields",
        "mining":                   "Mining Ops",
        "salvage":                  "Salvage Ops",
        "scanning":                 "Sensor Officer",
        "mobiglas":                 "MobiGlas / UI",
        "ui":                       "User Interface",
        "camera":                   "Camera Control",
        "checklists":               "Executive Officer",
        "on_foot":                  "Personal Suit AI",
        "eva":                      "EVA Suit",
        "ground_vehicle":           "Vehicle Ops",
        "chitchat":                 "Ship AI (chitchat)",
        "global":                   "Global / Mode Switch",
    }

    VOICE_DESCRIPTIONS = {
        "alan":     "UK male, calm British (ship AI)",
        "ryan":     "US male, neutral pilot",
        "lessac":   "US female, warm and clear",
        "joe":      "US male, military commander",
        "amy":      "US female, bright neutral",
        "kathleen": "US female, expressive",
        "kusal":    "US male, casual",
        "northern": "UK male, gruff northern English",
        "southern": "UK female, posh comms tower",
        "jenny":    "UK female, friendly conversational",
    }

    def __init__(self, state: AppState):
        self.state = state
        self.mic = MicLevel()
        self._last_command = "—"
        self._last_voice = "—"
        self._activity_lines = []  # ring buffer for dashboard preview

        self.root = ctk.CTk()
        self.root.title("CachePilot")
        self.root.geometry("1100x720")
        self.root.minsize(960, 600)
        self.root.configure(fg_color=COLOR_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self._refresh_loop()

    # ----- layout root -----
    def _build(self):
        # Two-column root: sidebar + content
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_content()
        self._show_tab("Dashboard")

    def _build_sidebar(self):
        side = ctk.CTkFrame(self.root, width=220, corner_radius=0,
                            fg_color=COLOR_PANEL)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_rowconfigure(99, weight=1)

        # Brand
        brand = ctk.CTkLabel(
            side, text="CachePilot",
            font=("Segoe UI", 18, "bold"),
            text_color=COLOR_ACCENT,
        )
        brand.grid(row=0, column=0, padx=18, pady=(18, 4), sticky="w")

        sub = ctk.CTkLabel(
            side, text="cockpit ops",
            font=("Segoe UI", 10),
            text_color=COLOR_DIM,
        )
        sub.grid(row=1, column=0, padx=18, pady=(0, 14), sticky="w")

        # Status pill
        self.status_pill = ctk.CTkLabel(
            side, text="● LISTENING", font=FONT_SUB,
            fg_color=COLOR_PANEL_HI, text_color=COLOR_GOOD,
            corner_radius=12,
        )
        self.status_pill.grid(row=2, column=0, padx=18, pady=(0, 12),
                              sticky="ew", ipadx=8, ipady=4)

        # Mic VU
        mic_lbl = ctk.CTkLabel(side, text="MIC", font=("Segoe UI", 9),
                               text_color=COLOR_DIM)
        mic_lbl.grid(row=3, column=0, padx=18, pady=(2, 2), sticky="w")
        self.mic_bar = ctk.CTkProgressBar(
            side, height=10, corner_radius=4,
            progress_color=COLOR_ACCENT,
            fg_color=COLOR_BG,
        )
        self.mic_bar.set(0)
        self.mic_bar.grid(row=4, column=0, padx=18, pady=(0, 18), sticky="ew")

        # Tab buttons
        tab_names = ["Dashboard", "Commands", "Crew", "Voices", "Logs", "Settings"]
        self._tab_buttons = {}
        for i, name in enumerate(tab_names):
            btn = ctk.CTkButton(
                side, text=name, anchor="w",
                font=FONT_BODY,
                fg_color="transparent",
                hover_color=COLOR_PANEL_HI,
                text_color=COLOR_TEXT,
                corner_radius=8,
                command=lambda n=name: self._show_tab(n),
                height=34,
            )
            btn.grid(row=5 + i, column=0, padx=10, pady=2, sticky="ew")
            self._tab_buttons[name] = btn

        # Quick mute toggle at the bottom
        self.mute_btn = ctk.CTkButton(
            side, text="Mute",
            font=FONT_SUB, height=40,
            fg_color=COLOR_ACCENT_DARK,
            hover_color=COLOR_ACCENT,
            command=self._on_mute,
        )
        self.mute_btn.grid(row=99, column=0, padx=14, pady=(0, 14), sticky="sew")

    def _build_content(self):
        self.content = ctk.CTkFrame(self.root, fg_color=COLOR_BG, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self._tab_frames = {}
        for name, builder in [
            ("Dashboard", self._build_dashboard),
            ("Commands",  self._build_commands),
            ("Crew",      self._build_crew),
            ("Voices",    self._build_voices),
            ("Logs",      self._build_logs),
            ("Settings",  self._build_settings),
        ]:
            frame = ctk.CTkFrame(self.content, fg_color=COLOR_BG, corner_radius=0)
            frame.grid(row=0, column=0, sticky="nsew")
            builder(frame)
            self._tab_frames[name] = frame

    # ----- tab switching -----
    def _show_tab(self, name):
        for n, f in self._tab_frames.items():
            f.grid_remove()
        self._tab_frames[name].grid()
        for n, b in self._tab_buttons.items():
            if n == name:
                b.configure(fg_color=COLOR_PANEL_HI, text_color=COLOR_ACCENT)
            else:
                b.configure(fg_color="transparent", text_color=COLOR_TEXT)

    # ===== DASHBOARD =====
    def _build_dashboard(self, parent):
        parent.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkLabel(parent, text="Dashboard", font=FONT_TITLE,
                           text_color=COLOR_TEXT)
        hdr.grid(row=0, column=0, padx=24, pady=(24, 4), sticky="w")

        sub = ctk.CTkLabel(parent, text="live cockpit telemetry",
                           font=FONT_BODY, text_color=COLOR_DIM)
        sub.grid(row=1, column=0, padx=24, pady=(0, 16), sticky="w")

        # Stat cards row
        cards = ctk.CTkFrame(parent, fg_color="transparent")
        cards.grid(row=2, column=0, padx=24, pady=(0, 16), sticky="ew")
        for i in range(4):
            cards.grid_columnconfigure(i, weight=1, uniform="card")

        self.card_status   = self._make_card(cards, "STATUS", "LISTENING", COLOR_GOOD)
        self.card_status.grid(row=0, column=0, padx=(0, 8), sticky="nsew")
        self.card_profile  = self._make_card(cards, "PROFILE", self.state.get_profile().upper(), COLOR_ACCENT)
        self.card_profile.grid(row=0, column=1, padx=8, sticky="nsew")
        self.card_copilot  = self._make_card(cards, "CO-PILOT", (self.state.get_copilot_voice() or "—").upper(), COLOR_ACCENT)
        self.card_copilot.grid(row=0, column=2, padx=8, sticky="nsew")
        self.card_voices   = self._make_card(cards, "VOICES", str(len(self.state.available_voices)), COLOR_ACCENT)
        self.card_voices.grid(row=0, column=3, padx=(8, 0), sticky="nsew")

        # Last command panel
        last = ctk.CTkFrame(parent, fg_color=COLOR_PANEL, corner_radius=12)
        last.grid(row=3, column=0, padx=24, pady=(0, 16), sticky="ew")
        ctk.CTkLabel(last, text="LAST COMMAND", font=("Segoe UI", 10, "bold"),
                     text_color=COLOR_DIM).pack(anchor="w", padx=18, pady=(14, 0))
        self.last_cmd_label = ctk.CTkLabel(
            last, text="—", font=("Segoe UI", 18, "bold"),
            text_color=COLOR_ACCENT,
        )
        self.last_cmd_label.pack(anchor="w", padx=18, pady=(2, 2))
        self.last_voice_label = ctk.CTkLabel(
            last, text="", font=FONT_BODY, text_color=COLOR_DIM,
        )
        self.last_voice_label.pack(anchor="w", padx=18, pady=(0, 14))

        # Recent activity preview
        actwrap = ctk.CTkFrame(parent, fg_color=COLOR_PANEL, corner_radius=12)
        actwrap.grid(row=4, column=0, padx=24, pady=(0, 24), sticky="nsew")
        parent.grid_rowconfigure(4, weight=1)
        ctk.CTkLabel(actwrap, text="RECENT ACTIVITY", font=("Segoe UI", 10, "bold"),
                     text_color=COLOR_DIM).pack(anchor="w", padx=18, pady=(14, 6))
        self.act_text = ctk.CTkTextbox(
            actwrap, font=FONT_MONO,
            fg_color=COLOR_BG, text_color=COLOR_TEXT,
            border_width=0, corner_radius=8,
        )
        self.act_text.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.act_text.configure(state="disabled")

    def _make_card(self, parent, title, value, accent):
        f = ctk.CTkFrame(parent, fg_color=COLOR_PANEL, corner_radius=12)
        ctk.CTkLabel(f, text=title, font=("Segoe UI", 9, "bold"),
                     text_color=COLOR_DIM).pack(anchor="w", padx=16, pady=(14, 0))
        lbl = ctk.CTkLabel(f, text=value, font=("Segoe UI", 20, "bold"),
                           text_color=accent)
        lbl.pack(anchor="w", padx=16, pady=(2, 14))
        f._value_label = lbl
        return f

    # ===== COMMANDS =====
    def _build_commands(self, parent):
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(parent, text="Commands", font=FONT_TITLE,
                     text_color=COLOR_TEXT).grid(
            row=0, column=0, columnspan=2, padx=24, pady=(24, 4), sticky="w")
        ctk.CTkLabel(parent, text="browse and edit voice commands",
                     font=FONT_BODY, text_color=COLOR_DIM).grid(
            row=1, column=0, columnspan=2, padx=24, pady=(0, 12), sticky="w")

        # Left: list of commands with search
        listwrap = ctk.CTkFrame(parent, fg_color=COLOR_PANEL, corner_radius=12)
        listwrap.grid(row=2, column=0, padx=(24, 8), pady=(0, 24), sticky="nsew")
        parent.grid_rowconfigure(2, weight=1)

        self.cmd_search = ctk.CTkEntry(
            listwrap, placeholder_text="Search commands...",
            fg_color=COLOR_BG, border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        self.cmd_search.pack(fill="x", padx=14, pady=14)
        self.cmd_search.bind("<KeyRelease>", lambda _e: self._refresh_command_list())

        # Listbox (still tk.Listbox — CTk doesn't ship one)
        self.cmd_listbox = tk.Listbox(
            listwrap, bg=COLOR_BG, fg=COLOR_TEXT,
            selectbackground=COLOR_ACCENT_DARK,
            selectforeground=COLOR_TEXT,
            borderwidth=0, highlightthickness=0,
            font=FONT_MONO,
        )
        self.cmd_listbox.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.cmd_listbox.bind("<<ListboxSelect>>", self._on_cmd_select)

        # Right: editor panel
        detwrap = ctk.CTkScrollableFrame(parent, fg_color=COLOR_PANEL,
                                         corner_radius=12)
        detwrap.grid(row=2, column=1, padx=(8, 24), pady=(0, 24), sticky="nsew")
        parent.grid_columnconfigure(0, weight=1, uniform="cmds")
        parent.grid_columnconfigure(1, weight=2, uniform="cmds")

        # Toolbar: New / Delete / Save
        toolbar = ctk.CTkFrame(detwrap, fg_color="transparent")
        toolbar.pack(fill="x", padx=14, pady=(8, 6))
        ctk.CTkButton(
            toolbar, text="+ New", width=80, font=FONT_SUB,
            fg_color=COLOR_GOOD, hover_color="#16a34a",
            command=self._cmd_new,
        ).pack(side="left")
        ctk.CTkButton(
            toolbar, text="Delete", width=80, font=FONT_SUB,
            fg_color=COLOR_BAD, hover_color="#dc2626",
            command=self._cmd_delete,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            toolbar, text="Save", width=80, font=FONT_SUB,
            fg_color=COLOR_ACCENT_DARK, hover_color=COLOR_ACCENT,
            command=self._cmd_save,
        ).pack(side="right")

        self.editor_status = ctk.CTkLabel(
            detwrap, text="Select a command on the left, or click + New.",
            font=FONT_BODY, text_color=COLOR_DIM, wraplength=480, justify="left",
        )
        self.editor_status.pack(anchor="w", padx=14, pady=(4, 10))

        # Name
        self._field_label(detwrap, "NAME (lowercase phrase users will say)")
        self.f_name = self._field_entry(detwrap)

        # Category
        self._field_label(detwrap, "CATEGORY")
        cat_values = list(self.DEPARTMENT_LABELS.keys())
        self.f_category = ctk.CTkOptionMenu(
            detwrap, values=cat_values,
            fg_color=COLOR_BG, button_color=COLOR_ACCENT_DARK,
            button_hover_color=COLOR_ACCENT,
        )
        self.f_category.pack(fill="x", padx=14, pady=(0, 10))

        # Type
        self._field_label(detwrap, "TYPE")
        self.f_type = ctk.CTkOptionMenu(
            detwrap, values=[
                "key", "hotkey", "hold", "hold_combo",
                "mouse", "scroll", "multi_action",
                "say", "unbound", "profile_switch",
            ],
            command=self._on_type_change,
            fg_color=COLOR_BG, button_color=COLOR_ACCENT_DARK,
            button_hover_color=COLOR_ACCENT,
        )
        self.f_type.pack(fill="x", padx=14, pady=(0, 10))

        # Type-specific fields (we show/hide based on selected type)
        self._field_label(detwrap, "KEY (for key / hold)")
        self.f_key = self._field_entry(detwrap)

        self._field_label(detwrap, "KEYS (comma-separated, for hotkey / hold_combo)")
        self.f_keys = self._field_entry(detwrap)

        self._field_label(detwrap, "DURATION SECONDS (for hold / hold_combo)")
        self.f_duration = self._field_entry(detwrap)

        self._field_label(detwrap, "MOUSE BUTTON (left / right / middle)")
        self.f_button = self._field_entry(detwrap)

        self._field_label(detwrap, "SCROLL DIRECTION (up / down) + AMOUNT")
        scroll_row = ctk.CTkFrame(detwrap, fg_color="transparent")
        scroll_row.pack(fill="x", padx=14, pady=(0, 10))
        self.f_scroll_dir = ctk.CTkOptionMenu(
            scroll_row, values=["up", "down"], width=100,
            fg_color=COLOR_BG, button_color=COLOR_ACCENT_DARK,
        )
        self.f_scroll_dir.pack(side="left")
        self.f_scroll_amount = ctk.CTkEntry(
            scroll_row, fg_color=COLOR_BG, border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        self.f_scroll_amount.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self._field_label(detwrap, "PROFILE NAME (for profile_switch)")
        self.f_profile = self._field_entry(detwrap)

        # Aliases (one per line)
        self._field_label(detwrap, "ALIASES (one per line)")
        self.f_aliases = ctk.CTkTextbox(
            detwrap, height=80, font=FONT_BODY,
            fg_color=COLOR_BG, text_color=COLOR_TEXT,
            border_width=0, corner_radius=8,
        )
        self.f_aliases.pack(fill="x", padx=14, pady=(0, 10))

        # Response variants
        self._field_label(detwrap, "RESPONSE VARIANTS (one per line)")
        self.f_responses = ctk.CTkTextbox(
            detwrap, height=120, font=FONT_BODY,
            fg_color=COLOR_BG, text_color=COLOR_TEXT,
            border_width=0, corner_radius=8,
        )
        self.f_responses.pack(fill="x", padx=14, pady=(0, 14))

        # Track which command is being edited (None = new)
        self._editing_name = None
        self._original_name = None
        self._cmd_cache = None

    def _field_label(self, parent, text):
        ctk.CTkLabel(
            parent, text=text, font=("Segoe UI", 9, "bold"),
            text_color=COLOR_DIM, anchor="w",
        ).pack(fill="x", padx=14, pady=(6, 2))

    def _field_entry(self, parent):
        e = ctk.CTkEntry(
            parent, fg_color=COLOR_BG, border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        e.pack(fill="x", padx=14, pady=(0, 10))
        return e

    def _on_type_change(self, _value=None):
        # Currently shows all fields. Future: hide irrelevant ones.
        pass

    def _load_base_commands(self):
        """Read commands/*.json directly (no aliases). Used only for the editor."""
        import glob
        out = {}
        for fp in sorted(glob.glob(os.path.join("commands", "*.json"))):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            default_cat = (data.get("_meta") or {}).get("category")
            for k, v in data.items():
                if k.startswith("_") or not isinstance(v, dict):
                    continue
                if default_cat:
                    v.setdefault("category", default_cat)
                out[k] = v
        return out

    def _refresh_command_list(self):
        if self._cmd_cache is None:
            self._cmd_cache = self._load_base_commands()
        q = (self.cmd_search.get() if hasattr(self, "cmd_search") else "").lower().strip()
        self.cmd_listbox.delete(0, "end")
        for k in sorted(self._cmd_cache.keys()):
            if q and q not in k.lower():
                continue
            self.cmd_listbox.insert("end", k)

    def _on_cmd_select(self, _e=None):
        sel = self.cmd_listbox.curselection()
        if not sel:
            return
        name = self.cmd_listbox.get(sel[0])
        entry = self._cmd_cache.get(name)
        if not entry:
            return
        self._populate_editor(name, entry)

    def _populate_editor(self, name, entry):
        self._editing_name = name
        self._original_name = name
        self.editor_status.configure(
            text=f"Editing: {name}", text_color=COLOR_ACCENT,
        )
        # Name
        self.f_name.delete(0, "end")
        self.f_name.insert(0, name)
        # Category
        cat = entry.get("category", "")
        if cat in self.f_category.cget("values"):
            self.f_category.set(cat)
        # Type
        atype = entry.get("type", "key")
        if atype in self.f_type.cget("values"):
            self.f_type.set(atype)
        # Type-specific fields
        self.f_key.delete(0, "end"); self.f_key.insert(0, entry.get("key", "") or "")
        keys = entry.get("keys") or []
        self.f_keys.delete(0, "end"); self.f_keys.insert(0, ", ".join(keys))
        self.f_duration.delete(0, "end")
        if "duration" in entry:
            self.f_duration.insert(0, str(entry["duration"]))
        self.f_button.delete(0, "end"); self.f_button.insert(0, entry.get("button", "") or "")
        if entry.get("direction"):
            self.f_scroll_dir.set(entry["direction"])
        self.f_scroll_amount.delete(0, "end")
        if "amount" in entry:
            self.f_scroll_amount.insert(0, str(entry["amount"]))
        self.f_profile.delete(0, "end")
        self.f_profile.insert(0, entry.get("profile", "") or "")
        # Aliases
        self.f_aliases.delete("1.0", "end")
        for a in entry.get("aliases", []) or []:
            self.f_aliases.insert("end", a + "\n")
        # Responses
        self.f_responses.delete("1.0", "end")
        resp = entry.get("response")
        if isinstance(resp, list):
            for r in resp:
                self.f_responses.insert("end", r + "\n")
        elif resp:
            self.f_responses.insert("end", resp + "\n")

    def _clear_editor(self):
        self._editing_name = None
        self._original_name = None
        self.editor_status.configure(text="New command — fill in fields and Save.",
                                     text_color=COLOR_GOOD)
        for w in [self.f_name, self.f_key, self.f_keys, self.f_duration,
                  self.f_button, self.f_scroll_amount, self.f_profile]:
            w.delete(0, "end")
        self.f_aliases.delete("1.0", "end")
        self.f_responses.delete("1.0", "end")

    def _gather_form(self):
        """Build the entry dict from form fields. Returns (name, entry, category)."""
        name = self.f_name.get().strip().lower()
        category = self.f_category.get().strip()
        atype = self.f_type.get().strip()
        entry = {"type": atype}

        if atype in ("key", "hold"):
            key = self.f_key.get().strip()
            if key:
                entry["key"] = key
        if atype in ("hotkey", "hold_combo"):
            raw = self.f_keys.get()
            keys = [k.strip() for k in raw.split(",") if k.strip()]
            if keys:
                entry["keys"] = keys
        if atype in ("hold", "hold_combo"):
            d = self.f_duration.get().strip()
            if d:
                try:
                    entry["duration"] = float(d)
                except ValueError:
                    pass
        if atype == "mouse":
            b = self.f_button.get().strip().lower()
            if b:
                entry["button"] = b
        if atype == "scroll":
            entry["direction"] = self.f_scroll_dir.get()
            try:
                entry["amount"] = int(self.f_scroll_amount.get().strip() or "1")
            except ValueError:
                entry["amount"] = 1
        if atype == "profile_switch":
            p = self.f_profile.get().strip()
            if p:
                entry["profile"] = p

        # Aliases
        aliases = [a.strip() for a in self.f_aliases.get("1.0", "end").splitlines() if a.strip()]
        if aliases:
            entry["aliases"] = aliases

        # Responses
        responses = [r.strip() for r in self.f_responses.get("1.0", "end").splitlines() if r.strip()]
        if len(responses) == 1:
            entry["response"] = responses[0]
        elif responses:
            entry["response"] = responses

        if category:
            entry["category"] = category

        return name, entry, category

    def _cmd_new(self):
        self._clear_editor()
        self.f_type.set("key")

    def _cmd_save(self):
        from main import write_command, delete_command
        name, entry, category = self._gather_form()
        if not name:
            self.editor_status.configure(text="Name is required.", text_color=COLOR_BAD)
            return
        # If we renamed, drop the old one before writing the new
        if self._original_name and self._original_name != name:
            delete_command(self._original_name)
        ok, path = write_command(name, entry, category)
        if ok:
            self.editor_status.configure(
                text=f"Saved to {path}. Click Reload (Settings) to apply.",
                text_color=COLOR_GOOD,
            )
            self.state.log(f"[CMD] Saved '{name}' to {path}")
            self._cmd_cache = None
            self._refresh_command_list()
            self._original_name = name
        else:
            self.editor_status.configure(
                text=f"Save failed: {path}", text_color=COLOR_BAD,
            )

    def _cmd_delete(self):
        from main import delete_command
        if not self._editing_name:
            self.editor_status.configure(text="Nothing selected to delete.",
                                         text_color=COLOR_BAD)
            return
        ok, path = delete_command(self._editing_name)
        if ok:
            self.editor_status.configure(
                text=f"Deleted from {path}. Click Reload (Settings) to apply.",
                text_color=COLOR_GOOD,
            )
            self.state.log(f"[CMD] Deleted '{self._editing_name}' from {path}")
            self._cmd_cache = None
            self._refresh_command_list()
            self._clear_editor()
        else:
            self.editor_status.configure(
                text=f"Delete failed: {path}", text_color=COLOR_BAD,
            )

    # ===== CREW =====
    def _build_crew(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(parent, text="Crew", font=FONT_TITLE,
                     text_color=COLOR_TEXT).grid(
            row=0, column=0, padx=24, pady=(24, 4), sticky="w")
        ctk.CTkLabel(parent, text="who answers from each department",
                     font=FONT_BODY, text_color=COLOR_DIM).grid(
            row=1, column=0, padx=24, pady=(0, 12), sticky="w")

        scroll = ctk.CTkScrollableFrame(parent, fg_color=COLOR_PANEL, corner_radius=12)
        scroll.grid(row=2, column=0, padx=24, pady=(0, 16), sticky="nsew")
        scroll.grid_columnconfigure(1, weight=1)

        self._crew_vars = {}
        current = self.state.get_assignments() or {}
        voices = self.state.available_voices

        for row, key in enumerate(self.DEPARTMENT_LABELS.keys()):
            label = self.DEPARTMENT_LABELS[key]
            ctk.CTkLabel(scroll, text=label, font=FONT_BODY,
                         text_color=COLOR_TEXT, anchor="w").grid(
                row=row, column=0, padx=14, pady=8, sticky="w")
            var = ctk.StringVar(value=current.get(key, voices[0] if voices else ""))
            self._crew_vars[key] = var
            menu = ctk.CTkOptionMenu(
                scroll, values=voices, variable=var, width=160,
                fg_color=COLOR_BG, button_color=COLOR_ACCENT_DARK,
                button_hover_color=COLOR_ACCENT,
                command=lambda v, k=key: self._on_crew_change(k, v),
            )
            menu.grid(row=row, column=1, padx=14, pady=8, sticky="w")

        # Footer with Save / Reset buttons
        foot = ctk.CTkFrame(parent, fg_color="transparent")
        foot.grid(row=3, column=0, padx=24, pady=(0, 24), sticky="ew")
        ctk.CTkButton(
            foot, text="Save to disk",
            fg_color=COLOR_ACCENT_DARK, hover_color=COLOR_ACCENT,
            command=self._on_crew_save,
        ).pack(side="right")

    def _on_crew_change(self, key, voice):
        self.state.update_assignment(key, voice)
        self.state.log(f"[CREW] {key} -> {voice}")

    def _on_crew_save(self):
        try:
            from main import save_voice_assignments
            ok = save_voice_assignments(self.state.get_assignments() or {})
            self.state.log("[CREW] Saved." if ok else "[CREW] Save failed.")
        except Exception as e:
            self.state.log(f"[CREW] Save error: {e}")

    # ===== VOICES =====
    def _build_voices(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(parent, text="Voices", font=FONT_TITLE,
                     text_color=COLOR_TEXT).grid(
            row=0, column=0, padx=24, pady=(24, 4), sticky="w")
        ctk.CTkLabel(parent, text="click any voice to hear a sample",
                     font=FONT_BODY, text_color=COLOR_DIM).grid(
            row=1, column=0, padx=24, pady=(0, 12), sticky="w")

        grid = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        grid.grid(row=2, column=0, padx=24, pady=(0, 24), sticky="nsew")
        for i in range(2):
            grid.grid_columnconfigure(i, weight=1, uniform="vc")

        for idx, v in enumerate(self.state.available_voices):
            r, c = divmod(idx, 2)
            card = ctk.CTkFrame(grid, fg_color=COLOR_PANEL, corner_radius=12)
            card.grid(row=r, column=c, padx=8, pady=8, sticky="nsew")

            ctk.CTkLabel(
                card, text=v.upper(),
                font=("Segoe UI", 16, "bold"), text_color=COLOR_ACCENT,
            ).pack(anchor="w", padx=18, pady=(14, 0))
            desc = self.VOICE_DESCRIPTIONS.get(v, "")
            ctk.CTkLabel(
                card, text=desc, font=FONT_BODY,
                text_color=COLOR_DIM, wraplength=380, justify="left",
            ).pack(anchor="w", padx=18, pady=(2, 10))

            ctk.CTkButton(
                card, text="▶ Test sample",
                font=FONT_SUB,
                fg_color=COLOR_ACCENT_DARK, hover_color=COLOR_ACCENT,
                command=lambda voice=v: self._test_voice(voice),
            ).pack(anchor="w", padx=18, pady=(0, 14))

    def _test_voice(self, voice):
        """Speak a sample line through this voice using the live TTS engine."""
        try:
            from main import get_tts_engine
            tts = get_tts_engine()
        except Exception:
            tts = None
        if tts is None:
            self.state.log(f"[VOICE] (no TTS) sample for {voice}")
            return
        samples = [
            "Flight ready, Captain.",
            "All systems nominal.",
            "Standing by for orders.",
            "Quantum drive online.",
        ]
        import random
        line = random.choice(samples)
        self.state.log(f"[VOICE] Testing {voice}: {line!r}")
        tts.speak(line, voice=voice)

    # ===== LOGS =====
    def _build_logs(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(parent, text="Activity log", font=FONT_TITLE,
                     text_color=COLOR_TEXT).grid(
            row=0, column=0, padx=24, pady=(24, 4), sticky="w")

        # Search bar
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=1, column=0, padx=24, pady=(0, 8), sticky="ew")
        self.log_search = ctk.CTkEntry(
            bar, placeholder_text="Filter logs...",
            fg_color=COLOR_PANEL, border_color=COLOR_BORDER,
            text_color=COLOR_TEXT, height=32,
        )
        self.log_search.pack(side="left", fill="x", expand=True)
        self.log_search.bind("<KeyRelease>", lambda _e: self._rerender_logs())
        ctk.CTkButton(
            bar, text="Clear", width=80,
            fg_color=COLOR_PANEL, hover_color=COLOR_PANEL_HI,
            command=self._clear_logs,
        ).pack(side="left", padx=(8, 0))

        # The text widget (tk.Text so we can use tags for color)
        wrap = ctk.CTkFrame(parent, fg_color=COLOR_PANEL, corner_radius=12)
        wrap.grid(row=2, column=0, padx=24, pady=(0, 24), sticky="nsew")
        self.log_text = tk.Text(
            wrap, bg=COLOR_PANEL, fg=COLOR_TEXT, font=FONT_MONO,
            borderwidth=0, highlightthickness=0, wrap="word",
        )
        self.log_text.pack(fill="both", expand=True, padx=12, pady=12)
        # Color tags
        self.log_text.tag_configure("info",  foreground=COLOR_TEXT)
        self.log_text.tag_configure("match", foreground=COLOR_ACCENT)
        self.log_text.tag_configure("say",   foreground=COLOR_GOOD)
        self.log_text.tag_configure("warn",  foreground=COLOR_WARN)
        self.log_text.tag_configure("err",   foreground=COLOR_BAD)
        self.log_text.tag_configure("dim",   foreground=COLOR_DIM)
        self.log_text.configure(state="disabled")

        self._all_log_lines = []

    def _clear_logs(self):
        self._all_log_lines.clear()
        self._rerender_logs()

    def _rerender_logs(self):
        q = (self.log_search.get() if hasattr(self, "log_search") else "").lower()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for line in self._all_log_lines:
            if q and q not in line.lower():
                continue
            tag = self._classify_log(line)
            self.log_text.insert("end", line + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _classify_log(self, line):
        if line.startswith("[ERROR]"):  return "err"
        if line.startswith("[WARN]"):   return "warn"
        if line.startswith("[MATCH]"):  return "match"
        if line.startswith("[SAY]"):    return "say"
        if line.startswith("[INFO]") or line.startswith("[INIT]"): return "dim"
        if line.startswith(("[ACTION]", "[INPUT]", "[NORMALIZED]", "[HEARD]")):
            return "dim"
        return "info"

    # ===== SETTINGS =====
    def _build_settings(self, parent):
        parent.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(parent, text="Settings", font=FONT_TITLE,
                     text_color=COLOR_TEXT).grid(
            row=0, column=0, padx=24, pady=(24, 16), sticky="w")

        box = ctk.CTkFrame(parent, fg_color=COLOR_PANEL, corner_radius=12)
        box.grid(row=1, column=0, padx=24, pady=(0, 24), sticky="ew")

        # Profile
        row = 0
        self._setting_row(box, row, "Profile",
                          ctk.CTkOptionMenu(
                              box, values=self.state.available_profiles,
                              variable=self._mk_var(self.state.get_profile()),
                              command=self._on_profile_change,
                              fg_color=COLOR_BG, button_color=COLOR_ACCENT_DARK,
                              button_hover_color=COLOR_ACCENT,
                          ))
        row += 1
        self._setting_row(box, row, "Co-pilot voice",
                          ctk.CTkOptionMenu(
                              box, values=self.state.available_voices,
                              variable=self._mk_var(self.state.get_copilot_voice() or "alan"),
                              command=self._on_copilot_change,
                              fg_color=COLOR_BG, button_color=COLOR_ACCENT_DARK,
                              button_hover_color=COLOR_ACCENT,
                          ))
        row += 1

        # Relay toggle
        self.relay_switch = ctk.CTkSwitch(
            box, text="Co-pilot relay",
            command=self._on_relay_toggle,
            progress_color=COLOR_ACCENT,
            text_color=COLOR_TEXT,
        )
        if self.state.get_relay_enabled():
            self.relay_switch.select()
        else:
            self.relay_switch.deselect()
        self.relay_switch.grid(row=row, column=0, columnspan=2, padx=20, pady=12, sticky="w")
        row += 1

        # Force voice
        self._setting_row(box, row, "Force voice (override)",
                          ctk.CTkOptionMenu(
                              box, values=["(off)"] + self.state.available_voices,
                              variable=self._mk_var(
                                  self.state.get_force_voice() or "(off)"
                              ),
                              command=self._on_force_voice_change,
                              fg_color=COLOR_BG, button_color=COLOR_ACCENT_DARK,
                              button_hover_color=COLOR_ACCENT,
                          ))
        row += 1

        ctk.CTkButton(
            box, text="Reload commands & voicelines",
            font=FONT_SUB, fg_color=COLOR_ACCENT_DARK, hover_color=COLOR_ACCENT,
            command=self._on_reload,
        ).grid(row=row, column=0, columnspan=2, padx=20, pady=20, sticky="w")

        # --- SC keymap XML override panel ---
        keymap_box = ctk.CTkFrame(parent, fg_color=COLOR_PANEL, corner_radius=12)
        keymap_box.grid(row=2, column=0, padx=24, pady=(0, 24), sticky="ew")

        ctk.CTkLabel(
            keymap_box, text="STAR CITIZEN KEYMAP",
            font=("Segoe UI", 11, "bold"), text_color=COLOR_ACCENT,
        ).pack(anchor="w", padx=20, pady=(14, 0))
        ctk.CTkLabel(
            keymap_box,
            text="Load an exported SC keybind XML to remap tagged commands "
                 "automatically. CachePilot scans your StarCitizen install for "
                 "mappings folders; pick one or browse manually.",
            font=FONT_BODY, text_color=COLOR_DIM,
            wraplength=720, justify="left",
        ).pack(anchor="w", padx=20, pady=(2, 10))

        # Discover files
        try:
            from scxml import discover_mapping_files, load_choice
            discovered = discover_mapping_files()
            current = load_choice()
        except Exception:
            discovered = []
            current = None

        # Dropdown of discovered files
        labels = ["(none)"] + [lbl for _fp, lbl in discovered]
        self._keymap_paths = {lbl: fp for fp, lbl in discovered}
        self._keymap_paths["(none)"] = None

        # Default selection: whatever's saved, if it's in the discovered list
        current_label = "(none)"
        if current:
            for fp, lbl in discovered:
                if os.path.abspath(fp) == os.path.abspath(current):
                    current_label = lbl
                    break
            else:
                # Saved path isn't in the discovered list — show its basename
                cur_label = f"(custom) {os.path.basename(current)}"
                labels.append(cur_label)
                self._keymap_paths[cur_label] = current
                current_label = cur_label

        row_picker = ctk.CTkFrame(keymap_box, fg_color="transparent")
        row_picker.pack(fill="x", padx=20, pady=(0, 8))

        self.keymap_var = ctk.StringVar(value=current_label)
        self.keymap_menu = ctk.CTkOptionMenu(
            row_picker, values=labels, variable=self.keymap_var,
            command=self._on_keymap_pick,
            fg_color=COLOR_BG, button_color=COLOR_ACCENT_DARK,
            button_hover_color=COLOR_ACCENT, width=420,
        )
        self.keymap_menu.pack(side="left")
        ctk.CTkButton(
            row_picker, text="Browse...", width=110,
            fg_color=COLOR_PANEL_HI, hover_color=COLOR_BORDER,
            command=self._on_keymap_browse,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            row_picker, text="Clear", width=80,
            fg_color=COLOR_BAD, hover_color="#dc2626",
            command=self._on_keymap_clear,
        ).pack(side="left", padx=(8, 0))

        # Status line
        self.keymap_status = ctk.CTkLabel(
            keymap_box, text=self._compose_keymap_status(),
            font=FONT_BODY, text_color=COLOR_DIM,
            wraplength=720, justify="left",
        )
        self.keymap_status.pack(anchor="w", padx=20, pady=(0, 14))

    def _compose_keymap_status(self):
        try:
            from scxml import load_choice, parse_mapping_file
        except Exception:
            return "scxml module not available."
        path = load_choice()
        if not path:
            return "No keymap selected. Using defaults from commands/*.json."
        try:
            parsed = parse_mapping_file(path)
        except Exception as e:
            return f"Could not parse: {e}"
        return f"Loaded: {os.path.basename(path)}  -  {len(parsed)} actions found. " \
               f"Click Reload above to apply."

    def _on_keymap_pick(self, label):
        from scxml import save_choice
        path = self._keymap_paths.get(label)
        if path is None:
            save_choice("")  # write empty so load_choice returns None
            self.state.log("[KEYMAP] Cleared.")
        else:
            save_choice(path)
            self.state.log(f"[KEYMAP] Selected {path}")
        self.keymap_status.configure(text=self._compose_keymap_status())

    def _on_keymap_browse(self):
        from tkinter import filedialog
        from scxml import get_default_search_folder, save_choice
        path = filedialog.askopenfilename(
            title="Select an SC keybind XML",
            initialdir=get_default_search_folder(),
            filetypes=[("SC keybind XML", "*.xml"), ("All files", "*.*")],
        )
        if not path:
            return
        save_choice(path)
        label = f"(custom) {os.path.basename(path)}"
        # Add to dropdown if missing
        vals = list(self.keymap_menu.cget("values"))
        if label not in vals:
            vals.append(label)
            self.keymap_menu.configure(values=vals)
            self._keymap_paths[label] = path
        self.keymap_var.set(label)
        self.state.log(f"[KEYMAP] Selected {path}")
        self.keymap_status.configure(text=self._compose_keymap_status())

    def _on_keymap_clear(self):
        from scxml import save_choice
        save_choice("")
        self.keymap_var.set("(none)")
        self.state.log("[KEYMAP] Cleared.")
        self.keymap_status.configure(text=self._compose_keymap_status())

    def _mk_var(self, value):
        v = ctk.StringVar(value=value)
        return v

    def _setting_row(self, parent, row, label, widget):
        ctk.CTkLabel(parent, text=label, font=FONT_BODY,
                     text_color=COLOR_TEXT, anchor="w").grid(
            row=row, column=0, padx=20, pady=8, sticky="w")
        widget.grid(row=row, column=1, padx=20, pady=8, sticky="w")

    # ===== handlers =====
    def _on_mute(self):
        muted = self.state.toggle_muted()
        self.state.log(f"[GUI] {'Muted' if muted else 'Unmuted'}")
        if muted:
            self.mute_btn.configure(text="Unmute", fg_color=COLOR_BAD)
        else:
            self.mute_btn.configure(text="Mute", fg_color=COLOR_ACCENT_DARK)

    def _on_reload(self):
        self.state.request_reload()
        self.state.log("[GUI] Reload requested")
        self._cmd_cache = None
        if hasattr(self, "cmd_listbox"):
            self._refresh_command_list()

    def _on_profile_change(self, value):
        self.state.set_profile(value)
        self.state.log(f"[GUI] Profile -> {value}")

    def _on_copilot_change(self, value):
        self.state.set_copilot_voice(value)
        self.state.log(f"[GUI] Co-pilot -> {value}")

    def _on_relay_toggle(self):
        enabled = bool(self.relay_switch.get())
        self.state.set_relay_enabled(enabled)
        self.state.log(f"[GUI] Relay {'on' if enabled else 'off'}")

    def _on_force_voice_change(self, value):
        if value == "(off)":
            self.state.set_force_voice(None)
            self.state.log("[GUI] Force voice off")
        else:
            self.state.set_force_voice(value)
            self.state.log(f"[GUI] Force voice -> {value}")

    def _on_close(self):
        self.state.request_stop()
        self.mic.stop()
        self.root.after(80, self.root.destroy)

    # ===== periodic refresh =====
    def _refresh_loop(self):
        # Drain log queue
        try:
            while True:
                line = self.state.log_queue.get_nowait()
                self._handle_log_line(line)
        except queue.Empty:
            pass

        # Mic VU
        if hasattr(self, "mic_bar"):
            self.mic_bar.set(self.mic.level)

        # Status pill
        if self.state.is_muted():
            self.status_pill.configure(text="● MUTED", text_color=COLOR_BAD)
            if hasattr(self, "card_status"):
                self.card_status._value_label.configure(text="MUTED", text_color=COLOR_BAD)
        else:
            self.status_pill.configure(text="● LISTENING", text_color=COLOR_GOOD)
            if hasattr(self, "card_status"):
                self.card_status._value_label.configure(text="LISTENING", text_color=COLOR_GOOD)

        # Dashboard card refresh
        if hasattr(self, "card_profile"):
            self.card_profile._value_label.configure(text=self.state.get_profile().upper())
        if hasattr(self, "card_copilot"):
            cp = self.state.get_copilot_voice()
            self.card_copilot._value_label.configure(
                text=(cp or "—").upper() if self.state.get_relay_enabled() else "OFF",
            )

        # First-time command list population (lazy)
        if hasattr(self, "cmd_listbox") and self._cmd_cache is None \
                and self._tab_frames["Commands"].winfo_viewable():
            self._refresh_command_list()

        if self.state.is_running():
            self.root.after(80, self._refresh_loop)

    def _handle_log_line(self, line):
        self._all_log_lines.append(line)
        # Keep buffer bounded
        if len(self._all_log_lines) > 2000:
            self._all_log_lines = self._all_log_lines[-1500:]

        # Append to Logs tab if it exists
        if hasattr(self, "log_text"):
            q = (self.log_search.get() if hasattr(self, "log_search") else "").lower()
            if not q or q in line.lower():
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n", self._classify_log(line))
                self.log_text.see("end")
                self.log_text.configure(state="disabled")

        # Dashboard preview ring (~last 8 lines)
        self._activity_lines.append(line)
        if len(self._activity_lines) > 8:
            self._activity_lines = self._activity_lines[-8:]
        if hasattr(self, "act_text"):
            self.act_text.configure(state="normal")
            self.act_text.delete("1.0", "end")
            self.act_text.insert("1.0", "\n".join(self._activity_lines))
            self.act_text.configure(state="disabled")

        # Update last-command card when we see a [MATCH] line
        if line.startswith("[MATCH] "):
            self._last_command = line[len("[MATCH] "):].strip()
            if hasattr(self, "last_cmd_label"):
                self.last_cmd_label.configure(text=self._last_command)
        elif line.startswith("[SAY] ("):
            # "[SAY] (voice) text"
            try:
                voice = line.split("(", 1)[1].split(")", 1)[0]
                self._last_voice = voice
                if hasattr(self, "last_voice_label"):
                    self.last_voice_label.configure(text=f"voice: {voice}")
            except Exception:
                pass

    def run(self):
        self.root.mainloop()
