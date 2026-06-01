SPEECH_ENGINE = "google"

USE_WAKE_WORD = False
WAKE_WORD = "ship"

SPEAK_CONFIRMATIONS = True

# Voice style for spoken confirmations:
#   "clean" — straight voice, no effects
#   "radio" — comms-radio bandpass + soft saturation + squelch beeps
#   "robot" — ring-modulated, bitcrushed synthetic voice
VOICE_STYLE = "radio"

# --- Department voices ------------------------------------------------------
# Each command has a `category` (set per-file via _meta or per-entry).
# When a command fires, the engine looks up its category here and speaks
# in that voice. Edit defaults at runtime via the GUI, or change them here.
# Persistent edits made in the GUI are saved to voice_assignments.json
# and override these defaults.
DEPARTMENT_VOICES = {
    "atc":                      "southern",   # comms tower
    "ship_flight":              "ryan",       # cockpit
    "power":                    "northern",   # engineering
    "ship_weapons":             "joe",        # tactical
    "targeting":                "joe",        # tactical
    "shields_countermeasures":  "joe",        # tactical
    "mining":                   "kusal",      # ops crew
    "salvage":                  "kusal",      # ops crew
    "scanning":                 "lessac",     # sensor officer
    "mobiglas":                 "amy",        # UI assistant
    "ui":                       "amy",        # UI assistant
    "camera":                   "amy",        # UI assistant
    "checklists":               "alan",       # XO
    "on_foot":                  "jenny",      # suit AI
    "eva":                      "jenny",      # suit AI
    "ground_vehicle":           "kusal",      # ops crew
    "chitchat":                 "alan",       # ship AI
    "global":                   "alan",       # mode switches
}

# Set this to a voice name to force every command through that voice
# (overrides DEPARTMENT_VOICES). Useful for testing. Leave as None for
# normal per-department routing.
FORCE_VOICE = None

# --- Co-pilot relay ---------------------------------------------------------
# When enabled, the co-pilot voice speaks first, addressing the crew member
# by name ("Roger, Alan, gear up.") before the actual department voice
# answers ("Gear going up."). This makes the ship feel crewed.
#
# Relay is skipped automatically for categories in COPILOT_RELAY_SKIP and
# when the destination voice is the same as the co-pilot (would be alan
# talking to himself).
COPILOT_RELAY_ENABLED = True
COPILOT_VOICE = "alan"
COPILOT_RELAY_SKIP = {"chitchat", "checklists", "global"}

# Piper voice to load from voices/<name>.onnx — the *default* voice used
# if a command has no category, no department mapping, and no override.
# UK voices:
#   "alan"     — en_GB male, calm British (ship-AI vibe)
#   "northern" — en_GB northern English male, gruff hangar-tech vibe
#   "southern" — en_GB southern English female, posh calm "Cortana"
#   "jenny"    — en_GB female, conversational
# US voices:
#   "ryan"     — en_US male, neutral
#   "joe"      — en_US male, deeper, military commander vibe
#   "lessac"   — en_US female, warm and clear
#   "amy"      — en_US female, bright neutral
#   "kathleen" — en_US female, expressive
#   "kusal"    — en_US male, casual
VOICE_NAME = "alan"

# --- Profiles ---------------------------------------------------------------
# A profile is a set of command categories that are active.
# Commands tagged with "global": true (or in chitchat) are always available.
# Switch profiles via voice ("ship mode" / "fps mode") or the GUI dropdown.
ACTIVE_PROFILE = "ship"

PROFILES = {
    "ship": [
        "atc", "ship_flight", "power", "targeting",
        "shields_countermeasures", "ship_weapons",
        "mining", "salvage", "scanning",
        "mobiglas", "camera", "ui", "checklists",
        "chitchat",
    ],
    "fps": [
        "on_foot", "eva", "mobiglas", "ui",
        "chitchat",
    ],
}

FUZZY_MATCH_ENABLED = True
FUZZY_MATCH_THRESHOLD = 0.82

COMMAND_COOLDOWN_SECONDS = 1.5

ENERGY_ADJUST_SECONDS = 1
PHRASE_TIME_LIMIT = 5

# Path to commands. Can be either a single JSON file or a directory
# containing multiple *.json files (which are merged at load time).
COMMANDS_JSON_PATH = "commands"

# Keyboard input backend. Options:
#   "pydirectinput" — pure Python, no driver install. Works for most games
#       when the terminal is run as administrator.
#   "interception"  — uses the Interception kernel driver. Bulletproof
#       against games / anti-cheats that filter SendInput. Requires the
#       Interception driver to be installed (one-time, admin + reboot).
#       Falls back to pydirectinput automatically if the driver is missing.
KEY_BACKEND = "pydirectinput"

DEBUG = True
