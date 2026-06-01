"""
CachePilot v0.1.0
Voice-activated Star Citizen cockpit assistant.

Listens on the microphone, converts speech to text, matches against an
editable JSON command catalogue, and triggers Star Citizen keybinds.
Speaks confirmations through local Piper voices with optional radio /
robot DSP effects. Ships with a co-pilot relay system that routes
commands to per-department voices (ATC, engineering, tactical, etc).
"""

import json
import random
import string
import time
import re
import sys
import threading
from difflib import get_close_matches


def pick_response(response):
    """Allow `response` to be a string or a list of strings (random pick)."""
    if isinstance(response, list):
        choices = [r for r in response if r]
        return random.choice(choices) if choices else ""
    return response or ""


def load_voicelines(folder="commands/voicelines"):
    """
    Load per-voice response overrides.
    Returns dict: {voice_name: {command_name: [response variants]}}.
    Missing folder is fine — overrides are optional.
    """
    import os, glob
    out = {}
    if not os.path.isdir(folder):
        return out
    for fp in sorted(glob.glob(os.path.join(folder, "*.json"))):
        voice = os.path.splitext(os.path.basename(fp))[0]
        try:
            with open(fp, "r", encoding="utf-8") as f:
                out[voice] = json.load(f)
        except Exception:
            # Silently ignore a bad override file; base lines still work
            continue
    return out


def pick_voice_response(voicelines, voice_name, command_name, fallback):
    """
    Look up a per-voice override for this command. Fall back to the
    base response list if no override exists.
    """
    voice_map = voicelines.get(voice_name) if voicelines else None
    if voice_map and command_name in voice_map:
        return pick_response(voice_map[command_name])
    return pick_response(fallback)


VOICE_OVERRIDES_PATH = "voice_assignments.json"


def load_voice_assignments():
    """
    Merge config.DEPARTMENT_VOICES with any persisted overrides from
    voice_assignments.json. The latter wins so the GUI can edit them.
    """
    import os
    base = dict(getattr(config, "DEPARTMENT_VOICES", {}) or {})
    if os.path.exists(VOICE_OVERRIDES_PATH):
        try:
            with open(VOICE_OVERRIDES_PATH, "r", encoding="utf-8") as f:
                overrides = json.load(f)
            if isinstance(overrides, dict):
                base.update({k: v for k, v in overrides.items() if v})
        except Exception:
            pass
    return base


def write_command(name, entry, category, target_file=None):
    """
    Write a single command into commands/<category>.json (creating the
    file if needed). If `target_file` is given, that filename is used
    instead of inferring from category.
    Returns (ok, path_written).
    """
    import os
    if not name:
        return False, "missing name"

    folder = "commands"
    os.makedirs(folder, exist_ok=True)

    # Decide which file to put the command in
    if target_file:
        path = os.path.join(folder, target_file)
        if not path.endswith(".json"):
            path += ".json"
    else:
        # Find a file whose _meta.category matches; fall back to <category>.json
        path = None
        import glob
        for fp in sorted(glob.glob(os.path.join(folder, "*.json"))):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            file_cat = (data.get("_meta") or {}).get("category")
            if file_cat == category:
                path = fp
                break
        if path is None:
            safe = (category or "misc").lower().replace(" ", "_")
            path = os.path.join(folder, f"{safe}.json")

    # Load or initialize the file
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False, f"could not read {path}"
    else:
        data = {"_meta": {"category": category}}

    # Strip per-entry category if it matches the file's default — keeps
    # the JSON clean.
    file_cat = (data.get("_meta") or {}).get("category")
    cleaned = dict(entry)
    if file_cat and cleaned.get("category") == file_cat:
        cleaned.pop("category", None)

    data[name] = cleaned

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return True, path
    except Exception as e:
        return False, str(e)


def delete_command(name):
    """
    Remove a command from whichever commands/*.json file holds it.
    Returns (ok, path_or_error).
    """
    import glob, os
    for fp in sorted(glob.glob(os.path.join("commands", "*.json"))):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if name in data:
            del data[name]
            try:
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                return True, fp
            except Exception as e:
                return False, str(e)
    return False, f"'{name}' not found in any file"


def save_voice_assignments(mapping):
    """Persist the user's department→voice edits."""
    try:
        with open(VOICE_OVERRIDES_PATH, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def build_copilot_relay(command_name, crew_voice):
    """
    Return a short co-pilot acknowledgement line addressing the crew
    member by voice name. Picks from a few variants at random.
    """
    import random
    name = (crew_voice or "crew").capitalize()
    cmd = command_name.lower()
    templates = [
        f"Roger, {name}. {cmd.capitalize()}.",
        f"Copy that, {name}. {cmd.capitalize()}.",
        f"{name}, {cmd}.",
        f"Acknowledged. {name}, {cmd}.",
    ]
    return random.choice(templates)


def should_relay(command_data, crew_voice, state=None):
    """Decide whether to play the co-pilot relay for this command."""
    # Live state overrides config when present
    if state is not None:
        enabled = state.get_relay_enabled()
        copilot = state.get_copilot_voice()
    else:
        enabled = bool(getattr(config, "COPILOT_RELAY_ENABLED", False))
        copilot = getattr(config, "COPILOT_VOICE", None)
    if not enabled:
        return False
    if not copilot or copilot == crew_voice:
        return False
    skip = set(getattr(config, "COPILOT_RELAY_SKIP", set()) or set())
    category = (command_data.get("category") or "").lower()
    if category in skip:
        return False
    return True


def resolve_voice_for_command(command_data, assignments, state):
    """
    Decide which voice should speak for this command:
      1. FORCE_VOICE in config (test override)
      2. GUI 'Force voice' setting (state)
      3. Per-category mapping in `assignments`
      4. State's current default voice
    """
    forced = getattr(config, "FORCE_VOICE", None)
    if forced:
        return forced
    gui_force = state.get_force_voice() if state else None
    if gui_force:
        return gui_force
    category = command_data.get("category")
    if category and category in assignments:
        return assignments[category]
    return state.get_voice() if state else None

import speech_recognition as sr

import config
from gui import AppState, CachePilotGUI
from input_backend import get_backend, normalize_key
from voice_fx import VoiceFX


def log(state, message):
    """Print and also push the line to the GUI log queue."""
    print(message)
    if state is not None:
        state.log(message)


def debug_log(state, message):
    """Log only if DEBUG is enabled in config."""
    if config.DEBUG:
        log(state, message)


def _expand_aliases(data, state=None):
    """
    For every entry with an `aliases` list, add a duplicate entry under
    each alias pointing to the same action data. Existing keys win.
    """
    added = 0
    for name, entry in list(data.items()):
        if not isinstance(entry, dict):
            continue
        aliases = entry.get("aliases") or []
        for alias in aliases:
            alias_norm = normalize(alias) if alias else ""
            if not alias_norm or alias_norm in data:
                continue
            data[alias_norm] = entry
            added += 1
    if added:
        debug_log(state, f"[INFO] Expanded {added} aliases.")
    return data


def _strip_meta(data):
    """
    Pull the _meta block (if any) and return (cleaned_dict, default_category,
    default_global). Drops every key starting with '_' from the returned dict.
    """
    meta = data.get("_meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    default_category = meta.get("category")
    default_global = bool(meta.get("global", False))
    cleaned = {k: v for k, v in data.items() if not k.startswith("_")}
    return cleaned, default_category, default_global


def load_commands(path, state=None):
    """
    Load voice commands. `path` may be either:
      - a JSON file path (legacy: single commands.json)
      - a directory: every *.json file inside is merged.

    Keys starting with '_' are treated as metadata and skipped.
    Later files override earlier ones if they declare the same command.
    """
    import os, glob

    files = []
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "*.json")))
        if not files:
            log(state, f"[ERROR] No JSON files in {path}.")
            return None
    elif os.path.isfile(path):
        files = [path]
    else:
        log(state, f"[ERROR] Path not found: {path}")
        return None

    merged = {}
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            log(state, f"[ERROR] {fp} is not valid JSON: {e}")
            return None
        data, default_cat, default_global = _strip_meta(data)
        data = {k: v for k, v in data.items() if isinstance(v, dict)}
        # Stamp the file's default category and global flag onto every
        # entry that didn't set them itself. Profiles filter by category.
        for entry in data.values():
            if default_cat:
                entry.setdefault("category", default_cat)
            if default_global:
                entry.setdefault("global", True)
        merged.update(data)
        debug_log(state, f"[INFO] Loaded {len(data)} from {os.path.basename(fp)}")

    # Apply SC keybind XML override before alias expansion so all aliases
    # of an action inherit the new key.
    merged, applied, total = apply_keymap_overrides(merged, state=state)
    if total > 0:
        log(state, f"[INFO] SC keymap: rebound {applied} of {total} "
                   f"tagged commands.")

    merged = _expand_aliases(merged, state=state)
    debug_log(state, f"[INFO] Total commands (after aliases): {len(merged)}")
    return merged


def apply_keymap_overrides(commands, state=None):
    """
    If a SC mapping XML is selected, override key/keys/type on every
    command whose `action` matches a parsed action. Returns
    (commands, applied_count, total_tagged_count).
    """
    try:
        from scxml import load_choice, parse_mapping_file
    except Exception:
        return commands, 0, 0
    path = load_choice()
    if not path:
        # Count tagged commands anyway so the GUI can report 0/N
        total = sum(1 for v in commands.values() if v.get("action"))
        return commands, 0, total
    mapping = parse_mapping_file(path)
    applied = 0
    total = 0
    for entry in commands.values():
        a = entry.get("action")
        if not a:
            continue
        total += 1
        override = mapping.get(a)
        if not override:
            continue
        # Wipe the old key/keys/button so the merge is clean
        for k in ("key", "keys", "button", "direction", "amount"):
            entry.pop(k, None)
        # Only update type if it's a key-like override. We keep "hold"
        # / "hold_combo" / "multi_action" types intact when SC just
        # changes the bound key — convert hold to hold with the new key.
        orig_type = entry.get("type")
        if orig_type in ("hold", "hold_combo") and override["type"] in ("key", "hotkey"):
            # User said "throttle up" should be a hold; XML says key W -> still hold W
            if override["type"] == "key":
                entry["type"] = "hold"
                entry["key"] = override["key"]
            else:  # hotkey
                entry["type"] = "hold_combo"
                entry["keys"] = override["keys"]
        else:
            entry.update(override)
        applied += 1
    return commands, applied, total


# Module-level handle for the GUI's voice-test buttons. Set by listen_loop.
_TTS_ENGINE = None


def get_tts_engine():
    """Return the live VoiceFX instance, or None if not yet initialized."""
    return _TTS_ENGINE


def init_tts(state=None):
    """Initialize the voice-effects TTS engine (or None on failure)."""
    if not config.SPEAK_CONFIRMATIONS:
        return None
    try:
        voice_name = state.get_voice() if state else getattr(
            config, "VOICE_NAME", "alan"
        )
        return VoiceFX(
            voice=voice_name,
            style=getattr(config, "VOICE_STYLE", "clean"),
            enabled=True,
            logger=lambda m: log(state, m) if state else print(m),
            engine=getattr(config, "TTS_ENGINE", "piper"),
        )
    except Exception as e:
        print(f"[WARN] Could not initialize voice FX TTS: {e}")
        return None


def speak(tts_engine, text, state=None, voice=None):
    """Queue text for speaking via VoiceFX (non-blocking)."""
    if voice:
        log(state, f"[SAY] ({voice}) {text}")
    else:
        log(state, f"[SAY] {text}")
    if tts_engine is None:
        return
    try:
        tts_engine.speak(text, voice=voice)
    except Exception as e:
        debug_log(state, f"[WARN] TTS failed: {e}")


def normalize(text):
    """Normalize recognized speech for matching."""
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_wake_word(text):
    """If wake word is required, only return the part after the wake word."""
    wake = config.WAKE_WORD.lower().strip()
    if text == wake:
        return ""
    prefix = wake + " "
    if text.startswith(prefix):
        return text[len(prefix):].strip()
    return None  # signal: wake word not present


def _profile_categories(profile_name):
    """Return the allowed categories for the named profile (empty = all)."""
    profiles = getattr(config, "PROFILES", None) or {}
    return set(profiles.get(profile_name, []))


def _is_command_in_profile(entry, allowed_categories):
    """Allowed if (no profile filter) or (global) or (category matches)."""
    if not allowed_categories:
        return True
    if entry.get("global"):
        return True
    return entry.get("category") in allowed_categories


def _collect_keywords(name, entry):
    """
    Build the keyword set for a command. Includes:
      - the command name itself
      - any aliases (already used for exact matching by _expand_aliases,
        but we also want them to match when buried in a sentence)
      - an explicit `keywords` list on the entry

    Each keyword is normalized to lowercase and wrapped in word boundaries
    at match time so "scan" doesn't fire on "scanner".
    """
    out = set()
    if name:
        out.add(name.lower().strip())
    for a in (entry.get("aliases") or []):
        if a:
            out.add(a.lower().strip())
    for k in (entry.get("keywords") or []):
        if k:
            out.add(k.lower().strip())
    return out


# Cache compiled patterns so we don't rebuild every utterance.
_KEYWORD_CACHE_KEY = None
_KEYWORD_CACHE = None


def _build_keyword_index(commands):
    """
    Return list of (command_name, set_of_keywords) for every dedupe-able
    command. Aliases that point to the same entry are collapsed.
    """
    global _KEYWORD_CACHE_KEY, _KEYWORD_CACHE
    # Cheap fingerprint of the commands dict to invalidate cache on reload.
    fp = id(commands), len(commands)
    if _KEYWORD_CACHE_KEY == fp and _KEYWORD_CACHE is not None:
        return _KEYWORD_CACHE

    # Deduplicate: many commands share the same dict (alias expansion
    # writes the same entry under multiple names). We want each unique
    # entry to appear once, keyed by the *canonical* name.
    seen_ids = set()
    index = []
    for name, entry in commands.items():
        if id(entry) in seen_ids:
            continue
        seen_ids.add(id(entry))
        kws = _collect_keywords(name, entry)
        if kws:
            index.append((name, kws))
    _KEYWORD_CACHE_KEY = fp
    _KEYWORD_CACHE = index
    return index


def _keyword_match(spoken, commands, allowed):
    """
    Look for any command whose keywords appear as whole words inside
    `spoken`. Returns the longest-keyword winner so "landing gear" beats
    "gear" when both fit.
    """
    index = _build_keyword_index(commands)
    best_name = None
    best_len = 0
    for name, kws in index:
        if not _is_command_in_profile(commands[name], allowed):
            continue
        for kw in kws:
            # Whole-word containment: "gear" matches "the gear down" but
            # not "geared" or "scanner".
            pattern = r"(?:^|\W)" + re.escape(kw) + r"(?:$|\W)"
            if re.search(pattern, spoken):
                if len(kw) > best_len:
                    best_name = name
                    best_len = len(kw)
                    break  # this command already won at length `best_len`
    return best_name


def match_command(spoken, commands, active_profile=None):
    """
    Tiered match:
      1. exact phrase (current behaviour)
      2. keyword-in-sentence match — fires when a known phrase is buried
         in a longer utterance like "put the gear down"
      3. fuzzy match — last resort
    All tiers respect profile filtering.
    """
    allowed = _profile_categories(active_profile) if active_profile else set()

    # Tier 1: exact match
    if spoken in commands and _is_command_in_profile(commands[spoken], allowed):
        return spoken

    # Tier 2: keyword-in-utterance
    if getattr(config, "KEYWORD_MATCH_ENABLED", True):
        hit = _keyword_match(spoken, commands, allowed)
        if hit:
            return hit

    # Tier 3: fuzzy
    if not config.FUZZY_MATCH_ENABLED:
        return None
    candidate_keys = [
        k for k, v in commands.items()
        if _is_command_in_profile(v, allowed)
    ]
    matches = get_close_matches(
        spoken, candidate_keys, n=1, cutoff=config.FUZZY_MATCH_THRESHOLD,
    )
    return matches[0] if matches else None


# --- Confirm-code (self-destruct) flow ------------------------------------

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0",
    "one": "1", "won": "1",
    "two": "2", "to": "2", "too": "2",
    "three": "3", "tree": "3",
    "four": "4", "for": "4", "fore": "4",
    "five": "5",
    "six": "6", "sex": "6",
    "seven": "7",
    "eight": "8", "ate": "8",
    "nine": "9", "niner": "9",
}


def _extract_digits(text):
    """
    Pull a digit sequence out of `text`. Accepts raw digits ("4626") and
    English number words ("four six two six"). Returns the concatenated
    digit string in the order they appear.
    """
    if not text:
        return ""
    out = []
    # Tokenize on word boundaries; keep digits and words separately.
    tokens = re.findall(r"\d+|[a-zA-Z]+", text.lower())
    for tok in tokens:
        if tok.isdigit():
            out.append(tok)
        elif tok in _DIGIT_WORDS:
            out.append(_DIGIT_WORDS[tok])
    return "".join(out)


def _speak_code(tts_engine, code, state, voice):
    """Speak a digit code one digit at a time so it's unmistakable."""
    spelled = " ".join(code)  # "4 6 2 6"
    intro = (f"To confirm self destruct, please confirm this code by "
             f"saying it: {spelled}.")
    speak(tts_engine, intro, state=state, voice=voice)


def _start_confirm_code(command_name, command_data, tts_engine, state, voice):
    """
    Begin a confirm_code flow:
      1. Generate a random N-digit code (default 4)
      2. Speak the prompt + code
      3. Stash the pending confirmation on AppState; the listen loop will
         match the user's spoken digits against it within `timeout` seconds.
    """
    if state is None:
        log(state, f"[ERROR] confirm_code '{command_name}' requires state.")
        return
    length = int(command_data.get("code_length", 4))
    timeout = float(command_data.get("confirm_timeout", 20.0))
    code = "".join(random.choices(string.digits, k=length))

    payload = {
        "code": code,
        "expires_at": time.time() + timeout,
        "command": command_name,
        "command_data": command_data,
        "voice": voice,
        "confirm_response": command_data.get(
            "confirm_response",
            "Self destructing in 30 seconds.",
        ),
    }
    state.set_pending_confirm(payload)
    log(state, f"[CONFIRM] '{command_name}' awaiting code {code} "
               f"(timeout {timeout:.0f}s).")
    _speak_code(tts_engine, code, state, voice)


def _handle_pending_confirm(spoken, state, tts_engine, backend):
    """
    Returns True if `spoken` was consumed by a pending confirm_code flow
    (either matched, cancelled by the user, or expired). When True, the
    normal command-matching pipeline should skip this utterance.
    """
    pending = state.get_pending_confirm() if state else None
    if not pending:
        return False

    # Expired? Clear and let normal matching handle the utterance.
    if time.time() > pending.get("expires_at", 0):
        state.clear_pending_confirm()
        log(state, "[CONFIRM] Confirmation window expired. Cancelled.")
        return False

    heard_digits = _extract_digits(spoken)
    if not heard_digits:
        debug_log(state, "[CONFIRM] No digits heard; ignoring utterance.")
        return False  # let normal matching also try

    if pending["code"] in heard_digits:
        state.clear_pending_confirm()
        log(state, f"[CONFIRM] Code matched. Firing '{pending['command']}'.")
        cmd_data = pending["command_data"]
        voice = pending.get("voice")
        # Speak the confirmation line, THEN fire the deferred keybind so
        # the in-game press lines up with "Self destructing in 30 seconds."
        confirm_text = pick_response(pending.get("confirm_response"))
        if confirm_text:
            speak(tts_engine, confirm_text, state=state, voice=voice)

        # Build the action to fire from the confirm_code entry. The
        # `deferred_type` field tells us what to do once confirmed; if
        # missing we default to "hold" (the self-destruct shape).
        deferred = {
            "type": (cmd_data.get("deferred_type") or "hold").lower(),
        }
        for k in ("key", "keys", "button", "duration", "amount",
                  "direction", "actions"):
            if k in cmd_data:
                deferred[k] = cmd_data[k]

        def fire_confirmed():
            try:
                if deferred["type"] == "multi_action":
                    for sub in deferred.get("actions", []):
                        _do_single_action(pending["command"], sub, backend,
                                          state=state)
                        time.sleep(0.08)
                else:
                    _do_single_action(pending["command"], deferred, backend,
                                      state=state)
            except Exception as e:
                log(state, f"[ERROR] confirmed action failed: {e}")

        if tts_engine is not None and confirm_text:
            tts_engine.run_after(fire_confirmed)
        else:
            fire_confirmed()
        return True

    log(state, f"[CONFIRM] Heard '{heard_digits}', expected "
               f"'{pending['code']}'. No match.")
    speak(tts_engine, "Code incorrect.", state=state,
          voice=pending.get("voice"))
    return True


def _do_single_action(command_name, action, backend, state=None):
    """Execute one action dict (key / hotkey / hold / mouse). No TTS here."""
    action_type = (action.get("type") or "").lower()

    if action_type == "key":
        key = action.get("key")
        if not key:
            log(state, f"[ERROR] '{command_name}' key action missing 'key'.")
            return
        send_key = normalize_key(key)
        debug_log(state, f"[INPUT] press key: {send_key} "
                         f"(backend={backend.name})")
        backend.press(send_key)

    elif action_type == "hotkey":
        keys = action.get("keys", [])
        if not keys:
            log(state, f"[ERROR] '{command_name}' hotkey action missing "
                       f"'keys'.")
            return
        send_keys = [normalize_key(k) for k in keys]
        debug_log(state, f"[INPUT] hotkey: {'+'.join(send_keys)} "
                         f"(backend={backend.name})")
        backend.hotkey(send_keys)

    elif action_type == "hold":
        key = action.get("key")
        duration = action.get("duration", 1.0)
        if not key:
            log(state, f"[ERROR] '{command_name}' hold action missing 'key'.")
            return
        send_key = normalize_key(key)
        debug_log(state, f"[INPUT] hold key: {send_key} for {duration}s "
                         f"(backend={backend.name})")
        backend.hold(send_key, duration)

    elif action_type == "mouse":
        button = (action.get("button") or "left").lower()
        debug_log(state, f"[INPUT] mouse click: {button} "
                         f"(backend={backend.name})")
        backend.mouse_click(button)

    elif action_type == "scroll":
        # amount > 0 = scroll up, amount < 0 = scroll down
        amount = action.get("amount", action.get("clicks", 1))
        direction = (action.get("direction") or "").lower()
        if direction == "down":
            amount = -abs(int(amount))
        elif direction == "up":
            amount = abs(int(amount))
        debug_log(state, f"[INPUT] scroll: {amount} (backend={backend.name})")
        backend.scroll(amount)

    elif action_type == "hold_combo":
        # Hold all but the last key, hold the last key for `duration`,
        # release in reverse. Used for camera move/save bindings (F4+arrow).
        keys = action.get("keys", [])
        duration = action.get("duration", 1.0)
        if not keys:
            log(state, f"[ERROR] '{command_name}' hold_combo missing 'keys'.")
            return
        send_keys = [normalize_key(k) for k in keys]
        debug_log(state, f"[INPUT] hold_combo: {'+'.join(send_keys)} for "
                         f"{duration}s (backend={backend.name})")
        backend.hold_combo(send_keys, duration)

    elif action_type == "unbound":
        # Nothing to press — just log. The response (if any) is spoken by
        # the caller via perform_action.
        debug_log(state, f"[INPUT] unbound: '{command_name}' (no key sent)")

    else:
        log(state, f"[ERROR] Unknown action type '{action_type}' inside "
                   f"'{command_name}'.")


def perform_action(command_name, command_data, tts_engine, backend,
                   state=None, voicelines=None, assignments=None):
    """Perform the action described by a command entry."""
    action_type = (command_data.get("type") or "").lower()
    # Pick the voice for this command (department-based, with overrides)
    voice = resolve_voice_for_command(command_data, assignments or {}, state)
    response = pick_voice_response(
        voicelines, voice, command_name,
        command_data.get("response", ""),
    )

    debug_log(state, f"[ACTION] type={action_type} command='{command_name}'")

    # Special action types that don't fit the speak-then-press pattern:
    # confirm_code starts a confirmation flow and returns early; mode/state
    # are handled inline and produce no keybind to defer.
    if action_type == "confirm_code":
        _start_confirm_code(command_name, command_data, tts_engine,
                            state=state, voice=voice)
        return

    # Commands flagged `clears_pending` cancel any open confirm_code flow
    # (e.g. "cancel self destruct" both clears the prompt and fires the
    # same hold-backspace keybind to abort the in-game timer).
    if command_data.get("clears_pending") and state is not None:
        if state.get_pending_confirm():
            state.clear_pending_confirm()
            log(state, f"[CONFIRM] '{command_name}' cleared pending "
                       f"confirmation.")

    if action_type in ("mode", "profile_switch"):
        target = (command_data.get("profile")
                  or command_data.get("mode")
                  or command_data.get("target"))
        if target and state is not None:
            state.set_profile(target)
            log(state, f"[PROFILE] Switched to {target!r}.")
        else:
            log(state, f"[WARN] '{command_name}' missing profile target.")
        if response:
            _speak_with_optional_relay(command_name, command_data, response,
                                       voice, tts_engine, state)
        return

    # Build the keypress as a callable so we can either fire it immediately
    # or defer it until after the crew finishes speaking (relay mode).
    def fire_action():
        try:
            if action_type == "multi_action":
                actions = command_data.get("actions", [])
                if not actions:
                    log(state, f"[ERROR] Command '{command_name}' has no "
                               f"'actions' list.")
                    return
                for sub in actions:
                    _do_single_action(command_name, sub, backend, state=state)
                    time.sleep(0.08)

            elif action_type in ("speak", "say"):
                pass  # speak-only: no key to send

            elif action_type == "state_toggle":
                log(state, f"[STATE] '{command_name}' (state tracking not "
                           f"implemented in v0.0.1)")
                if command_data.get("key"):
                    _do_single_action(
                        command_name,
                        {"type": "key", "key": command_data["key"]},
                        backend,
                        state=state,
                    )

            elif action_type in (
                "key", "hotkey", "hold", "mouse", "scroll",
                "hold_combo", "unbound",
            ):
                _do_single_action(command_name, command_data, backend,
                                  state=state)

            else:
                log(state, f"[ERROR] Unknown action type '{action_type}' "
                           f"for '{command_name}'.")
        except Exception as e:
            log(state, f"[ERROR] Action '{command_name}' failed: {e}")

    # When relay is active the order should read in-game as:
    #   1. co-pilot acknowledges  ("Roger, Joe. Fire weapon group one.")
    #   2. crew member replies    ("Primaries hot.")
    #   3. THEN the keybind fires
    # Otherwise (no relay) we keep the legacy "press first, speak after"
    # behaviour so simple commands stay snappy.
    relaying = bool(response) and should_relay(command_data, voice, state=state)
    if relaying and tts_engine is not None:
        copilot_voice = (state.get_copilot_voice() if state else None) \
                        or getattr(config, "COPILOT_VOICE", None)
        relay_text = build_copilot_relay(command_name, voice)
        speak(tts_engine, relay_text, state=state, voice=copilot_voice)
        speak(tts_engine, response, state=state, voice=voice)
        tts_engine.run_after(fire_action)
    else:
        fire_action()
        if response:
            speak(tts_engine, response, state=state, voice=voice)


def _speak_with_optional_relay(command_name, command_data, response, voice,
                               tts_engine, state):
    """Speak a response with the co-pilot relay prepended when appropriate."""
    if should_relay(command_data, voice, state=state):
        copilot_voice = (state.get_copilot_voice() if state else None) \
                        or getattr(config, "COPILOT_VOICE", None)
        relay_text = build_copilot_relay(command_name, voice)
        speak(tts_engine, relay_text, state=state, voice=copilot_voice)
    speak(tts_engine, response, state=state, voice=voice)


def listen_once(recognizer, microphone, stt_engine=None, state=None):
    """Listen for a single phrase and return recognized text (or None)."""
    with microphone as source:
        debug_log(state, "[LISTEN] Waiting for speech...")
        try:
            # Use a short overall timeout so the loop can check mute/stop flags
            # between phrases instead of blocking forever.
            audio = recognizer.listen(
                source,
                timeout=2,
                phrase_time_limit=config.PHRASE_TIME_LIMIT,
            )
        except sr.WaitTimeoutError:
            return None
        except Exception as e:
            debug_log(state, f"[WARN] Listen failed: {e}")
            return None

    try:
        if stt_engine is not None:
            text = stt_engine.transcribe(audio, recognizer)
        else:
            text = recognizer.recognize_google(audio)
        if text:
            debug_log(state, f"[HEARD] {text}")
            return text
        debug_log(state, "[HEARD] (unintelligible)")
        return None
    except sr.UnknownValueError:
        debug_log(state, "[HEARD] (unintelligible)")
        return None
    except sr.RequestError as e:
        log(state, f"[ERROR] Speech recognition request failed: {e}")
        log(state, "       Check your internet connection.")
        return None


def listen_loop(state: AppState):
    """Background-thread loop: listen, match, and trigger actions."""
    commands = load_commands(config.COMMANDS_JSON_PATH, state=state)
    if commands is None:
        state.request_stop()
        return

    voicelines = load_voicelines()
    if voicelines:
        log(state, f"[INFO] Loaded voice overrides for: "
                   f"{', '.join(sorted(voicelines.keys()))}")

    assignments = load_voice_assignments()
    log(state, f"[INFO] Loaded {len(assignments)} department voice assignments.")
    # Sync to state so the GUI editor reads from the same source of truth
    if state is not None:
        state.set_assignments(assignments)

    tts_engine = init_tts(state=state)
    # Expose for the GUI's "test voice" buttons
    global _TTS_ENGINE
    _TTS_ENGINE = tts_engine

    # Initialize STT engine (Whisper local or Google online).
    from stt import get_engine as get_stt_engine
    stt_engine = get_stt_engine(
        getattr(config, "SPEECH_ENGINE", "whisper"),
        model_size=getattr(config, "WHISPER_MODEL", "base"),
        device=getattr(config, "WHISPER_DEVICE", "cpu"),
        logger=lambda m: log(state, m),
    )

    # Warm *every* voice the user could possibly trigger this session:
    # active crew first (so the first reply has no hitch), then every
    # other available voice so the Voices tab + Force-voice + Crew
    # dropdown switches are all instant.
    if tts_engine is not None and state is not None:
        priority = []
        seen = set()
        for v in [state.get_voice(), state.get_copilot_voice(),
                  state.get_force_voice()]:
            if v and v not in seen:
                priority.append(v); seen.add(v)
        for v in (state.get_assignments() or {}).values():
            if v and v not in seen:
                priority.append(v); seen.add(v)
        background = [v for v in state.available_voices if v not in seen]
        tts_engine.warmup(priority, background)

    # Initialize the configured input backend, with auto-fallback.
    backend = get_backend(
        getattr(config, "KEY_BACKEND", "pydirectinput"),
        logger=lambda m: log(state, m),
    )

    recognizer = sr.Recognizer()
    try:
        microphone = sr.Microphone()
    except Exception as e:
        log(state, f"[ERROR] Could not open microphone: {e}")
        log(state, "       Is PyAudio installed and is a microphone connected?")
        state.request_stop()
        return

    # Calibrate for background noise once at startup
    with microphone as source:
        log(state, f"[INIT] Adjusting for ambient noise "
                   f"({config.ENERGY_ADJUST_SECONDS}s)...")
        recognizer.adjust_for_ambient_noise(
            source, duration=config.ENERGY_ADJUST_SECONDS
        )
        log(state, f"[INIT] Energy threshold: {recognizer.energy_threshold:.1f}")

    if config.USE_WAKE_WORD:
        log(state, f"[INFO] Wake word enabled: say '{config.WAKE_WORD}' before "
                   f"each command.")
    else:
        log(state, "[INFO] Wake word disabled. Listening for any known command.")

    last_action_time = 0.0

    while state.is_running():
        try:
            # Handle reload request from the GUI
            if state.consume_reload():
                reloaded = load_commands(config.COMMANDS_JSON_PATH, state=state)
                if reloaded is not None:
                    commands = reloaded
                    voicelines = load_voicelines()
                    assignments = load_voice_assignments()
                    if state is not None:
                        state.set_assignments(assignments)
                    log(state, "[INFO] Commands, voicelines, and assignments reloaded.")

            # Handle voice-change request from the GUI
            new_voice = state.consume_voice_change()
            if new_voice and tts_engine is not None:
                if tts_engine.set_voice(new_voice):
                    log(state, f"[INFO] Voice changed to {new_voice}.")
                else:
                    log(state, f"[WARN] Could not switch to voice '{new_voice}'.")

            # If muted, skip listening entirely so we don't burn CPU or
            # accidentally fire actions when the user comes back.
            if state.is_muted():
                time.sleep(0.2)
                continue

            heard = listen_once(recognizer, microphone,
                                stt_engine=stt_engine, state=state)
            if not heard:
                continue

            # Re-check mute after potentially long network round-trip
            if state.is_muted():
                debug_log(state, "[SKIP] Muted during recognition.")
                continue

            normalized = normalize(heard)
            debug_log(state, f"[NORMALIZED] {normalized}")

            if config.USE_WAKE_WORD:
                stripped = strip_wake_word(normalized)
                if stripped is None:
                    debug_log(state, "[SKIP] Wake word not detected.")
                    continue
                if stripped == "":
                    debug_log(state, "[SKIP] Only wake word heard, no command.")
                    continue
                normalized = stripped
                debug_log(state, f"[COMMAND] {normalized}")

            # Pending confirm_code (e.g. self-destruct): if active, give it
            # first shot at the utterance. It only "consumes" the line if
            # it matched, cancelled, or fired — otherwise we fall through
            # to normal matching so the user can still issue commands.
            if _handle_pending_confirm(normalized, state, tts_engine, backend):
                last_action_time = time.time()
                continue

            now = time.time()
            if now - last_action_time < config.COMMAND_COOLDOWN_SECONDS:
                debug_log(state, "[SKIP] Cooldown active, ignoring command.")
                continue

            match = match_command(
                normalized, commands, active_profile=state.get_profile()
            )
            if match is None:
                debug_log(state, f"[NO MATCH] '{normalized}' did not match any "
                                 f"command.")
                continue

            log(state, f"[MATCH] {match}")
            # Live-edit pickup: if the GUI saved new assignments, use them
            if state is not None:
                live = state.get_assignments()
                if live:
                    assignments = live
            perform_action(
                match, commands[match], tts_engine, backend,
                state=state, voicelines=voicelines, assignments=assignments,
            )
            last_action_time = time.time()

        except Exception as e:
            log(state, f"[ERROR] Unexpected error: {e}")
            time.sleep(0.5)

    log(state, "[EXIT] Listener stopped. Fly safe!")


def is_admin():
    """Return True if this process is running with administrator rights."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def main():
    print("=" * 60)
    print(" CachePilot v0.1.0")
    print("=" * 60)
    print("CachePilot sends keyboard input to the currently active "
          "window. Click into Star Citizen before using commands.")
    print("Use the GUI window to mute or quit.")
    if not is_admin():
        print("[WARN] Not running as administrator. Star Citizen may ignore "
              "key presses. Right-click your terminal and choose "
              "'Run as administrator', then re-launch.")
    print("-" * 60)

    state = AppState(
        profile=getattr(config, "ACTIVE_PROFILE", "ship"),
        voice=getattr(config, "VOICE_NAME", "alan"),
        profiles=list((getattr(config, "PROFILES", {}) or {}).keys()) or ["ship", "fps"],
        voices=[
            "alan", "ryan", "lessac",          # original 3
            "joe", "amy", "kathleen", "kusal", # added US voices
            "northern", "southern", "jenny",   # added UK voices
        ],
        relay_enabled=getattr(config, "COPILOT_RELAY_ENABLED", True),
        copilot_voice=getattr(config, "COPILOT_VOICE", "alan"),
    )

    # Background thread runs the listen loop; daemon=True so Ctrl+C in the
    # console also kills it cleanly if the GUI ever crashes.
    listener_thread = threading.Thread(
        target=listen_loop, args=(state,), daemon=True
    )
    listener_thread.start()

    try:
        gui = CachePilotGUI(state)
        gui.run()
    except KeyboardInterrupt:
        pass
    finally:
        state.request_stop()
        listener_thread.join(timeout=3.0)


if __name__ == "__main__":
    main()
