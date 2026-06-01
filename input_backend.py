"""
Input backend abstraction for CachePilot.

Two backends are supported:

* "pydirectinput" — pure-Python, no driver install. Sends DirectInput-style
  scan codes. Works for most games. Requires the terminal to be running
  as administrator for Star Citizen to accept the input.

* "interception" — uses the Interception kernel driver
  (https://github.com/oblitum/Interception). The driver makes synthesized
  input indistinguishable from a real keyboard, so games and anti-cheats
  that filter SendInput-based events still accept it. Requires the
  Interception driver to be installed system-wide (one-time, admin +
  reboot).

The backend is selected via config.KEY_BACKEND. If "interception" is
selected and the driver isn't available, we fall back to pydirectinput
and log a warning, so the app still runs.
"""

import time


# pydirectinput uses names like "altleft", "ctrlleft", "shiftleft".
# interception-python accepts the same names. We normalize a few common
# variants from commands.json (e.g. "left alt", "leftalt") to the canonical
# form so both backends accept the same JSON.
KEY_ALIASES = {
    "leftalt": "altleft",
    "left alt": "altleft",
    "rightalt": "altright",
    "right alt": "altright",
    "leftctrl": "ctrlleft",
    "left ctrl": "ctrlleft",
    "rightctrl": "ctrlright",
    "right ctrl": "ctrlright",
    "leftshift": "shiftleft",
    "left shift": "shiftleft",
    "rightshift": "shiftright",
    "right shift": "shiftright",
}


def normalize_key(name):
    """Translate a key name from commands.json into the canonical form."""
    if not name:
        return name
    key = name.strip().lower()
    return KEY_ALIASES.get(key, key)


class PyDirectInputBackend:
    """Sends keys via pydirectinput (DirectInput-style scan codes)."""

    name = "pydirectinput"

    def __init__(self):
        import pydirectinput
        pydirectinput.FAILSAFE = False
        self._pdi = pydirectinput

    def press(self, key):
        self._pdi.press(key)

    def hotkey(self, keys):
        # Hold modifiers, tap final key, release modifiers in reverse.
        for k in keys[:-1]:
            self._pdi.keyDown(k)
        self._pdi.press(keys[-1])
        for k in reversed(keys[:-1]):
            self._pdi.keyUp(k)

    def hold(self, key, duration):
        """Press and hold `key` for `duration` seconds, then release."""
        self._pdi.keyDown(key)
        try:
            time.sleep(max(0.0, float(duration)))
        finally:
            self._pdi.keyUp(key)

    def mouse_click(self, button):
        """Click a mouse button: 'left', 'right', or 'middle'."""
        # pydirectinput.click() accepts button name directly
        self._pdi.click(button=button)

    def scroll(self, amount):
        """Mouse wheel scroll. Positive = up, negative = down."""
        self._pdi.scroll(int(amount))

    def hold_combo(self, keys, duration):
        """
        Hold modifier keys, hold final key for `duration` seconds, release
        all in reverse. e.g. ['f4','right'] holds F4, holds Right for 1s,
        releases Right, releases F4.
        """
        for k in keys:
            self._pdi.keyDown(k)
        try:
            time.sleep(max(0.0, float(duration)))
        finally:
            for k in reversed(keys):
                self._pdi.keyUp(k)


class InterceptionBackend:
    """Sends keys via the Interception kernel driver."""

    name = "interception"

    def __init__(self):
        import interception
        # auto_capture_devices finds the physical keyboard device the
        # driver is forwarding for. If the driver isn't installed this
        # raises and we fall back in get_backend().
        interception.auto_capture_devices(keyboard=True, mouse=False)
        self._ic = interception

    def press(self, key):
        self._ic.press(key)

    def hotkey(self, keys):
        # Same pattern as pydirectinput: down-down-press-up-up.
        for k in keys[:-1]:
            self._ic.key_down(k)
        # Brief sleep so the game registers modifiers before the final key
        time.sleep(0.02)
        self._ic.press(keys[-1])
        for k in reversed(keys[:-1]):
            self._ic.key_up(k)

    def hold(self, key, duration):
        """Press and hold `key` for `duration` seconds, then release."""
        self._ic.key_down(key)
        try:
            time.sleep(max(0.0, float(duration)))
        finally:
            self._ic.key_up(key)

    def mouse_click(self, button):
        """Click a mouse button: 'left', 'right', or 'middle'."""
        # interception-python exposes MouseButton + click helpers
        button = (button or "left").strip().lower()
        if button == "left":
            self._ic.left_click()
        elif button == "right":
            # Right-click via mouse_down/up on the right button
            self._ic.mouse_down(button="right")
            time.sleep(0.05)
            self._ic.mouse_up(button="right")
        elif button == "middle":
            self._ic.mouse_down(button="middle")
            time.sleep(0.05)
            self._ic.mouse_up(button="middle")
        else:
            raise ValueError(f"Unknown mouse button: {button}")

    def hold_combo(self, keys, duration):
        """Hold modifier keys, hold final key for `duration`, release all."""
        for k in keys:
            self._ic.key_down(k)
        try:
            time.sleep(max(0.0, float(duration)))
        finally:
            for k in reversed(keys):
                self._ic.key_up(k)

    def scroll(self, amount):
        """Mouse wheel scroll. Positive = up, negative = down."""
        # interception-python doesn't expose a high-level scroll helper,
        # so emit raw mouse stroke events.
        from interception import MouseStroke, MouseFlag, get_mouse
        mouse = get_mouse()
        if mouse is None:
            return
        stroke = MouseStroke(
            state=0,
            flags=MouseFlag.MOUSE_MOVE_RELATIVE,
            rolling=int(amount) * 120,  # 120 units per wheel notch
        )
        stroke.state |= 0x0400  # MOUSE_WHEEL flag
        self._ic.send(mouse, stroke)


def get_backend(name, logger=None):
    """
    Return an input backend instance.

    `name` should be "pydirectinput" or "interception". If "interception"
    is requested but the driver isn't available, falls back to
    pydirectinput and logs a warning.

    `logger` is an optional callable that takes a single string argument
    (used to surface fallback warnings into the GUI activity log).
    """
    def _log(msg):
        if logger:
            logger(msg)
        else:
            print(msg)

    requested = (name or "pydirectinput").strip().lower()

    if requested == "interception":
        try:
            backend = InterceptionBackend()
            _log("[INFO] Using Interception driver for keyboard input.")
            return backend
        except ImportError:
            _log("[WARN] 'interception-python' not installed. "
                 "Install it with: pip install interception-python")
        except Exception as e:
            _log(f"[WARN] Interception driver unavailable ({e}). "
                 f"Is the Interception driver installed? "
                 f"See README for setup steps.")
        _log("[INFO] Falling back to pydirectinput.")
        requested = "pydirectinput"

    if requested == "pydirectinput":
        backend = PyDirectInputBackend()
        _log("[INFO] Using pydirectinput for keyboard input.")
        return backend

    # Unknown name — default to pydirectinput
    _log(f"[WARN] Unknown KEY_BACKEND '{name}', defaulting to pydirectinput.")
    return PyDirectInputBackend()
