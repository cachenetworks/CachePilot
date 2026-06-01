"""
Star Citizen keybind XML parser for CachePilot.

SC exports keybinds as XML like:

    <ActionMaps ...>
      <actionmap name="spaceship_general">
        <action name="v_toggle_all_doorlocks">
          <rebind input="kb1_rctrl+np_2"/>
        </action>
      </actionmap>
    </ActionMaps>

This module:
- discovers mapping XML files in the user's SC install
- parses one of them into a dict {action_name: cachepilot_action_dict}
- translates SC input syntax (kb1_rctrl+np_2) to the pydirectinput names
  used in our commands.json (ctrlright, num2)

CachePilot commands carry an optional "action" field. At load time, if
a mapping is selected, we override the command's key/keys/type with
whatever the XML says for that action name.
"""

import glob
import os
import re
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# Key name translation: SC -> pydirectinput
# ---------------------------------------------------------------------------

# Maps SC token (no kb1_ prefix, lowercased) to pydirectinput name.
# pydirectinput names from KEYBOARD_MAPPING in the library.
KEY_MAP = {
    # Letters and digits use their literal characters.
    # Function keys.
    "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4", "f5": "f5",
    "f6": "f6", "f7": "f7", "f8": "f8", "f9": "f9", "f10": "f10",
    "f11": "f11", "f12": "f12",

    # Modifiers (both sides).
    "lalt": "altleft",   "ralt": "altright",
    "lctrl": "ctrlleft", "rctrl": "ctrlright",
    "lshift": "shiftleft","rshift": "shiftright",
    "alt": "alt", "ctrl": "ctrl", "shift": "shift",

    # Whitespace and edits.
    "space": "space", "tab": "tab", "enter": "enter", "return": "enter",
    "escape": "esc", "esc": "esc",
    "backspace": "backspace", "delete": "del", "del": "del",
    "insert": "insert", "ins": "insert",

    # Navigation.
    "home": "home", "end": "end",
    "pgup": "pageup", "pageup": "pageup",
    "pgdn": "pagedown", "pagedown": "pagedown",
    "up": "up", "down": "down", "left": "left", "right": "right",

    # Punctuation.
    "tilde": "`", "grave": "`",
    "minus": "-", "equals": "=",
    "lbracket": "[", "rbracket": "]",
    "semicolon": ";", "apostrophe": "'", "quote": "'",
    "comma": ",", "period": ".", "slash": "/", "backslash": "\\",

    # Numpad ("np_X" or "numpad_X" in SC).
    "np_0": "num0", "np_1": "num1", "np_2": "num2", "np_3": "num3",
    "np_4": "num4", "np_5": "num5", "np_6": "num6", "np_7": "num7",
    "np_8": "num8", "np_9": "num9",
    "np_add": "add", "np_subtract": "subtract",
    "np_multiply": "multiply", "np_divide": "divide",
    "np_decimal": "decimal", "np_enter": "enter",
    "numpad0": "num0", "numpad1": "num1", "numpad2": "num2",
    "numpad3": "num3", "numpad4": "num4", "numpad5": "num5",
    "numpad6": "num6", "numpad7": "num7", "numpad8": "num8", "numpad9": "num9",
}

# Tokens that look like a single character: digits 0-9 and letters a-z.
SINGLE_CHAR = re.compile(r"^[a-z0-9]$")

# Mouse tokens that we map to mouse buttons.
MOUSE_MAP = {
    "mouse1": "left",   "left":   "left",
    "mouse2": "right",  "right":  "right",
    "mouse3": "middle", "middle": "middle",
    "wheel_up":   ("scroll", "up"),
    "wheel_down": ("scroll", "down"),
    "wheelup":    ("scroll", "up"),
    "wheeldown":  ("scroll", "down"),
}


def _translate_token(tok):
    """Translate one SC token (no prefix) to a pydirectinput key name, or None."""
    t = tok.strip().lower()
    if not t:
        return None
    if t in KEY_MAP:
        return KEY_MAP[t]
    if SINGLE_CHAR.match(t):
        return t
    return None


def parse_input(spec):
    """
    Translate a SC input string into a CachePilot action dict.

    Examples:
      "kb1_n"               -> {"type": "key", "key": "n"}
      "kb1_lalt+n"          -> {"type": "hotkey", "keys": ["altleft", "n"]}
      "kb1_rctrl+np_2"      -> {"type": "hotkey", "keys": ["ctrlright", "num2"]}
      "mo1_left"            -> {"type": "mouse", "button": "left"}
      "mo1_wheel_up"        -> {"type": "scroll", "direction": "up", "amount": 3}
      ""                    -> None  (deliberately unbound)
      "js1_button1"         -> None  (joystick: unsupported in v0.1)
    """
    if not spec:
        return None
    raw = spec.strip().lower()

    # Strip kb1_ prefix from each '+' segment. SC writes the prefix once
    # at the start ("kb1_lalt+n"), but we handle both forms defensively.
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    if not parts:
        return None

    # Determine device from the first part's prefix
    first = parts[0]
    if first.startswith(("js1_", "js2_", "gp1_")):
        return None  # joystick / gamepad: unsupported

    # Mouse?
    if first.startswith("mo1_") or first.startswith("mouse"):
        mtok = first.split("_", 1)[1] if "_" in first else first
        mapped = MOUSE_MAP.get(mtok)
        if mapped is None:
            return None
        if isinstance(mapped, tuple) and mapped[0] == "scroll":
            return {"type": "scroll", "direction": mapped[1], "amount": 3}
        return {"type": "mouse", "button": mapped}

    # Keyboard. Strip kb1_ from each part.
    cleaned = []
    for p in parts:
        if p.startswith("kb1_") or p.startswith("kb2_"):
            p = p.split("_", 1)[1]
        cleaned.append(p)

    # Re-translate. np_2 contains an underscore which our strip-by-first-_
    # also handles, but we want to keep "np_2" intact for KEY_MAP lookup —
    # check before stripping.
    translated = []
    for raw_tok in parts:
        # Re-derive the raw token preserving "np_" prefix
        if raw_tok.startswith(("kb1_", "kb2_")):
            tok = raw_tok.split("_", 1)[1]  # "np_2" -> "np_2" (only first _ stripped)
            # Wait, that strips "np" not "kb1". Actually split removes "kb1"
            # and we end up with "np_2" intact. Good.
        else:
            tok = raw_tok
        out = _translate_token(tok)
        if out is None:
            return None
        translated.append(out)

    if len(translated) == 1:
        return {"type": "key", "key": translated[0]}
    return {"type": "hotkey", "keys": translated}


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def parse_mapping_file(path):
    """
    Parse one SC mapping XML. Returns dict {action_name: cachepilot_action}.
    Actions with no usable rebind are simply absent from the dict.
    """
    try:
        tree = ET.parse(path)
    except Exception:
        return {}
    root = tree.getroot()

    out = {}
    for amap in root.iter("actionmap"):
        for action in amap.findall("action"):
            name = action.get("name")
            if not name:
                continue
            # Take the first non-empty rebind
            chosen = None
            for rb in action.findall("rebind"):
                spec = rb.get("input", "")
                if spec:
                    chosen = spec
                    break
            if chosen is None:
                continue
            translated = parse_input(chosen)
            if translated:
                out[name] = translated
    return out


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

# Channels the SC launcher uses. We probe each under the user's profile
# and also Program Files.
SC_CHANNELS = ["LIVE", "PTU", "EPTU", "HOTFIX", "4.0_PREVIEW", "TECH-PREVIEW"]


def _candidate_roots():
    """All paths that might contain the SC install."""
    roots = []
    user = os.environ.get("USERPROFILE", "")
    if user:
        roots.append(os.path.join(user, "Roberts Space Industries", "StarCitizen"))
    # Common install locations
    for base in (r"C:\Program Files\Roberts Space Industries\StarCitizen",
                 r"C:\Program Files (x86)\Roberts Space Industries\StarCitizen",
                 r"D:\Roberts Space Industries\StarCitizen",
                 r"E:\Roberts Space Industries\StarCitizen"):
        roots.append(base)
    return roots


def discover_mapping_files():
    """
    Find all .xml files in any SC mappings folder we can locate.
    Returns list of (path, label) tuples sorted by mtime descending.
    The label is just the basename for display.
    """
    found = []
    seen = set()
    for root in _candidate_roots():
        if not os.path.isdir(root):
            continue
        for channel in SC_CHANNELS:
            folder = os.path.join(
                root, channel, "user", "client", "0", "controls", "mappings",
            )
            if not os.path.isdir(folder):
                continue
            for fp in glob.glob(os.path.join(folder, "*.xml")):
                if fp in seen:
                    continue
                seen.add(fp)
                try:
                    mtime = os.path.getmtime(fp)
                except OSError:
                    mtime = 0.0
                found.append((fp, channel, mtime))

    found.sort(key=lambda t: t[2], reverse=True)
    return [(fp, f"{channel}: {os.path.basename(fp)}") for fp, channel, _ in found]


def get_default_search_folder():
    """A sensible folder to start the file-picker dialog from."""
    user = os.environ.get("USERPROFILE", "")
    if user:
        candidate = os.path.join(user, "Roberts Space Industries", "StarCitizen")
        if os.path.isdir(candidate):
            return candidate
        return user
    return os.path.expanduser("~")


# ---------------------------------------------------------------------------
# Persisting the user's chosen mapping
# ---------------------------------------------------------------------------

CHOICE_PATH = "keymap_choice.json"


def load_choice():
    """Return the previously selected mapping path, or None."""
    import json
    if not os.path.exists(CHOICE_PATH):
        return None
    try:
        with open(CHOICE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        path = data.get("path")
        return path if path and os.path.exists(path) else None
    except Exception:
        return None


def save_choice(path):
    """Persist the selected mapping path. Empty string clears the choice."""
    import json
    try:
        if not path:
            # Clear by removing the file (load_choice returns None)
            if os.path.exists(CHOICE_PATH):
                os.remove(CHOICE_PATH)
            return True
        with open(CHOICE_PATH, "w", encoding="utf-8") as f:
            json.dump({"path": path}, f, indent=2)
        return True
    except Exception:
        return False
