# CachePilot

**Voice-activated Star Citizen cockpit assistant. Local TTS, multi-voice crew, radio-comms vibe.**

CachePilot listens to your microphone, recognises spoken commands, and
fires the matching Star Citizen keybind. It then talks back through a
local neural TTS engine in one of ten voices, routed to whichever crew
member "owns" that department — so requesting landing is answered by
your ATC voice, firing weapons is acknowledged by your tactical voice,
and a co-pilot can relay the order in between.

No cloud. No subscription. No game memory reading. Just clean keyboard
input, the same way VoiceAttack-style tools have worked for years.

---

## Highlights

- **Speech recognition** — Google Web Speech via `SpeechRecognition`.
- **Local neural TTS** — [Piper](https://github.com/rhasspy/piper),
  10 voices included, real-time on CPU.
- **Radio FX** — DSP bandpass + soft saturation + squelch beeps so
  every line sounds like comms over a handheld.
- **Per-department voice routing** — ATC is one voice, engineering is
  another, tactical is a third, etc. Fully reassignable in the UI.
- **Co-pilot relay** — optional middleman voice that addresses the
  crew before they answer.
- **440+ commands** out of the box: flight, ATC, weapons, shields,
  targeting, mining, salvage, scanning, on-foot, EVA, cameras,
  checklists, chit-chat.
- **Editable in-app** — add, edit, rename, delete commands without
  touching JSON files.
- **Modern dashboard** — built with CustomTkinter. Tabs for Dashboard,
  Commands, Crew, Voices, Logs, Settings. Live mic VU meter.
- **Two input backends** — `pydirectinput` for everyday use,
  `interception` (kernel driver) as a fallback if anti-cheat is being
  difficult.

---

## How it works

```
   you say "landing gear"
            │
            ▼
   ┌────────────────────┐
   │ SpeechRecognition  │  Google online STT
   └─────────┬──────────┘
             │ "landing gear"
             ▼
   ┌────────────────────┐
   │  normalize + match │  exact + fuzzy (difflib)
   └─────────┬──────────┘
             │ matched: "landing mode"  (ship_flight)
             ▼
   ┌────────────────────┐
   │ pydirectinput.press │  sends "n" to Star Citizen
   └─────────┬──────────┘
             │
             ▼
   ┌────────────────────┐
   │  pick voice (ryan) │  per-department mapping
   ├────────────────────┤
   │  Piper synth        │  voice .onnx model
   ├────────────────────┤
   │  radio FX           │  bandpass + squelch
   ├────────────────────┤
   │  sounddevice play   │
   └────────────────────┘
        "Copy. Toggling landing mode."
```

---

## Quick start (Windows)

### 1. Clone

```cmd
git clone https://github.com/cachenetworks/CachePilot.git
cd CachePilot
```

### 2. Run setup

```cmd
setup.bat
```

This:
- creates a `.venv` virtual environment
- installs all Python dependencies
- downloads the 10 Piper voice models (~620 MB) into `voices/`

### 3. Launch

**Important:** Star Citizen ignores synthesized input from non-admin
processes. Run as administrator.

```cmd
.venv\Scripts\activate
python main.py
```

Click into the Star Citizen window, say something like:

- "flight ready"
- "request landing"
- "landing gear"
- "engines on"
- "boost"
- "scan"

You'll hear the matching crew member reply.

---

## Voices included

| Voice | Accent | Gender | Role suggestion |
|---|---|---|---|
| **alan** | UK | Male | Ship AI / XO (calm British) |
| **ryan** | US | Male | Cockpit / Flight (neutral pilot) |
| **lessac** | US | Female | Sensor officer (warm) |
| **joe** | US | Male | Tactical (military commander) |
| **amy** | US | Female | MobiGlas / UI (bright, eager) |
| **kathleen** | US | Female | Dramatic / theatrical |
| **kusal** | US | Male | Mining / Ops (casual) |
| **northern** | UK | Male | Engineering (gruff Northern English) |
| **southern** | UK | Female | ATC tower (posh) |
| **jenny** | UK | Female | Suit AI / EVA (friendly) |

Click **Voices** tab → **▶ Test sample** on any voice to hear it.

---

## The UI

Six tabs in the sidebar:

- **Dashboard** — status pill, mic VU bar, last command card, recent
  activity preview.
- **Commands** — search every command, click to edit. Buttons for
  **+ New**, **Save**, **Delete**. Changes write back to
  `commands/<category>.json` and take effect on Reload.
- **Crew** — pick a voice for each department. **Save to disk** writes
  to `voice_assignments.json` so your assignments survive restart.
- **Voices** — catalogue of all 10 voices with description and a Test
  button.
- **Logs** — color-coded activity feed with filter search.
- **Settings** — profile (ship/fps), co-pilot voice + toggle, force
  voice override, fuzzy threshold (in `config.py`), reload button.

---

## Profiles

A profile is a set of command categories that are active. Two ship:

- **ship** — atc, flight, power, targeting, shields, weapons, mining,
  salvage, scanning, mobiglas, camera, ui, checklists, chit-chat.
- **fps** — on-foot, eva, mobiglas, ui, chit-chat.

Switch with the voice commands **"ship mode"** / **"fps mode"** or in
the Settings tab. Commands not in the active profile won't match —
so saying "flight ready" while on foot won't accidentally power down
your ship from across the verse.

---

## Co-pilot relay

When enabled, two voices speak for each command:

1. The **co-pilot** (default: alan) addresses the crew member by name:
   *"Roger, Joe. Fire weapon group one."*
2. The **crew voice** (joe, for tactical) answers:
   *"Group one firing, boss!"*

Toggle in **Settings**. Auto-skipped for chit-chat, checklists,
global commands, and any command whose crew voice is already the
co-pilot (otherwise alan answers himself).

---

## Editing commands

Two ways:

**In-app (recommended):** Commands tab → click a command → edit fields
→ Save. New ones via **+ New**, remove via **Delete**.

**Hand-edit JSON:** `commands/*.json` are organised by category.
Each entry looks like:

```json
"request landing": {
  "type": "hotkey",
  "keys": ["leftalt", "n"],
  "response": [
    "Requesting landing, Captain.",
    "Hailing tower for landing.",
    "Landing request sent.",
    "We are inbound."
  ],
  "aliases": ["hail landing", "landing request", "call landing"]
}
```

### Supported action types

| Type | Behaviour |
|---|---|
| `key` | Press one key |
| `hotkey` | Press a modifier combo |
| `hold` | Hold a key for `duration` seconds |
| `hold_combo` | Hold modifier(s) + tap final key, hold for `duration` |
| `mouse` | Click `left` / `right` / `middle` |
| `scroll` | Mouse wheel up/down × amount |
| `multi_action` | Run a list of actions in order |
| `say` | Speak only — no key press |
| `unbound` | Speak a "not bound" message (placeholder) |
| `profile_switch` | Switch active profile |

### Aliases & response variants

- **`aliases`** is a list. Every alias becomes its own routable phrase.
- **`response`** can be a single string or a list. With a list, the
  engine picks one at random each time the command fires.

### Per-voice responses

`commands/voicelines/<voice>.json` lets each voice have its own line
for every command. The engine first checks the active voice's override,
then falls back to the base `response`.

So "flight ready" is:
- alan: *"As you wish, Captain. Flight ready."*
- joe: *"Hooah. Flight ready."*
- southern: *"Naturally, Commander. Flight ready."*

Edit these JSONs to customise crew personalities.

---

## Configuration

`config.py` holds non-UI settings:

| Key | What it does |
|---|---|
| `VOICE_STYLE` | `"clean"`, `"radio"` (default), `"robot"` |
| `VOICE_NAME` | Default voice when no department matches |
| `COPILOT_VOICE` | The relay voice |
| `COPILOT_RELAY_ENABLED` | Default relay on/off |
| `DEPARTMENT_VOICES` | Default category → voice mapping |
| `FORCE_VOICE` | Test override; every command uses this voice |
| `FUZZY_MATCH_THRESHOLD` | 0.0–1.0, higher = stricter (default 0.82) |
| `COMMAND_COOLDOWN_SECONDS` | Min gap between firings (default 1.5) |
| `KEY_BACKEND` | `"pydirectinput"` or `"interception"` |
| `PROFILES` | Per-profile allowed categories |
| `ACTIVE_PROFILE` | Startup profile |

---

## Troubleshooting

### Star Citizen ignores my key presses

Run the terminal **as administrator** before `python main.py`. Synthesized
input from non-admin processes is filtered by the OS for elevated /
EAC-protected windows.

If admin still doesn't work, switch to the Interception driver:

1. Download from <https://github.com/oblitum/Interception/releases>.
2. Elevated `cmd` → `install-interception.exe /install`.
3. **Reboot.**
4. In `config.py` set `KEY_BACKEND = "interception"`.
5. Re-run CachePilot. Look for `[INFO] Using Interception driver`.

### Microphone not detected

Windows → Settings → Privacy & security → Microphone → enable for
desktop apps. Confirm your mic is the default input device. The mic
VU bar in the sidebar should pulse when you speak.

### "PyAudio install failed" during setup.bat

```cmd
pip install pipwin
pipwin install pyaudio
```

Or grab a matching wheel from
<https://www.lfd.uci.edu/~gohlke/pythonlibs/> and `pip install` it.

### Speech recognition fails

`recognize_google` needs an internet connection. If you have one and
it still fails, your IP may be temporarily rate-limited — wait a few
minutes.

### Voice sounds wrong / not loading

Look for `[TTS] Voice file missing for 'X'` in the Logs tab. Re-run
`setup.bat` to download missing models, or delete `voices/<name>.onnx`
and re-run.

### App crashed Star Citizen

Almost always a coincidence — SC's I/O crashes are independent of
external input. Check `%LOCALAPPDATA%\Star Citizen\Crashes\` for the
real cause. CachePilot only sends keystrokes, no memory access.

---

## Project layout

```
CachePilot/
├─ main.py                  # listen loop + action dispatcher
├─ gui.py                   # CustomTkinter dashboard
├─ voice_fx.py              # Piper TTS + radio DSP
├─ input_backend.py         # pydirectinput / interception
├─ config.py                # tunables
├─ requirements.txt
├─ setup.bat                # one-shot installer
├─ commands/
│  ├─ _global.json          # profile switches
│  ├─ chitchat.json         # conversational lines
│  ├─ flight.json, atc.json, weapons.json, ... (17 files)
│  └─ voicelines/
│     └─ <voice>.json       # 10 voice-character JSONs
└─ voices/                  # Piper .onnx models (downloaded by setup.bat)
```

---

## Requirements

- **Windows 10/11** (the input backends are Windows-only)
- **Python 3.10+** (3.11 tested)
- **Working microphone**
- **~700 MB disk** (Piper voices + dependencies)
- **Internet** (Google STT)

---

## Known limitations (v0.1.0)

- Online speech recognition only. Offline (Whisper, Vosk) is a future
  task.
- Doesn't read Star Citizen's exported keybind XML. If you remapped
  keys in-game, edit `commands/*.json` to match.
- No joystick / HOTAS support yet.
- Default SC keybinds change between patches. The bindings shipped
  here match the 2024–2025 default layout.
- Windows-only. POSIX backends not provided.

---

## Credits

- **[Piper](https://github.com/rhasspy/piper)** — local neural TTS.
- **[SpeechRecognition](https://github.com/Uberi/speech_recognition)**
  — STT wrapper.
- **[CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)**
  — the dashboard look.
- **[pydirectinput](https://github.com/learncodebygaming/pydirectinput)**
  — DirectInput-style key sending.
- **[Interception](https://github.com/oblitum/Interception)** —
  kernel-level keyboard driver for the bulletproof fallback.
- **Star Citizen** — © Cloud Imperium Games. CachePilot is unofficial
  and unaffiliated.

---

## License

MIT. See [LICENSE](LICENSE).
