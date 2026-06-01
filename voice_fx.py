"""
Voice effects for CachePilot.

Synthesizer: Piper (local neural TTS). Voices live in voices/*.onnx.
Falls back to pyttsx3 if Piper or a voice file isn't available.

Styles (DSP applied after synthesis):
- "clean" : raw voice
- "radio" : narrow bandpass + soft saturation + squelch beeps
- "robot" : ring-mod + bitcrush
"""

import os
import queue
import threading
import wave

import numpy as np
from scipy.signal import butter, sosfilt
import sounddevice as sd


# --- DSP -------------------------------------------------------------------

def _bandpass(samples, sr, lo, hi, order=4):
    sos = butter(order, [lo, hi], btype="band", fs=sr, output="sos")
    return sosfilt(sos, samples)


def _soft_clip(samples, drive=2.0):
    return np.tanh(samples * drive)


def _ring_mod(samples, sr, freq=80.0):
    t = np.arange(len(samples)) / sr
    return samples * np.sin(2 * np.pi * freq * t)


def _bitcrush(samples, bits=6):
    steps = 2 ** bits
    return np.round(samples * steps) / steps


def _squelch_beep(sr, freq=1200.0, duration=0.05, volume=0.15):
    t = np.arange(int(sr * duration)) / sr
    return np.sin(2 * np.pi * freq * t) * volume


def _apply_radio(samples, sr):
    out = _bandpass(samples, sr, 300, 3400, order=6)
    out = _soft_clip(out, drive=1.8)
    peak = np.max(np.abs(out)) or 1.0
    out = out / peak * 0.9
    beep = _squelch_beep(sr)
    return np.concatenate([beep, out, beep * 0.6])


def _apply_robot(samples, sr):
    out = _ring_mod(samples, sr, freq=70.0)
    out = _bitcrush(out, bits=5)
    out = _bandpass(out, sr, 200, 5000, order=4)
    peak = np.max(np.abs(out)) or 1.0
    return out / peak * 0.9


# --- Synthesizer backends --------------------------------------------------

VOICES_DIR = "voices"


class PiperSynth:
    """Piper voice synthesis. One instance per voice file."""

    name = "piper"

    def __init__(self, voice_name):
        from piper import PiperVoice
        path = os.path.join(VOICES_DIR, f"{voice_name}.onnx")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Piper voice not found: {path}")
        self.voice = PiperVoice.load(path)
        self.sample_rate = self.voice.config.sample_rate
        self.voice_name = voice_name

    def synthesize(self, text):
        """Return (samples_float32_mono, sample_rate)."""
        chunks = []
        for c in self.voice.synthesize(text):
            chunks.append(np.frombuffer(c.audio_int16_bytes, dtype=np.int16))
        if not chunks:
            return np.zeros(0, dtype=np.float32), self.sample_rate
        all_int16 = np.concatenate(chunks)
        samples = all_int16.astype(np.float32) / 32768.0
        return samples, self.sample_rate


class KokoroSynth:
    """
    Higher-quality neural TTS using kokoro-onnx. Single shared ONNX model
    (~310 MB) with multiple voice embeddings (~27 MB total). Slower than
    Piper on CPU but noticeably more natural-sounding.

    Voice names map our 10 Piper-style names onto Kokoro's pre-baked voices.
    """

    name = "kokoro"

    # Map our Piper-named voices to the closest Kokoro voice. Kokoro uses
    # codes like af_sarah (american female sarah), am_michael, bf_emma, etc.
    VOICE_MAP = {
        "alan":     "bm_george",    # british male, calm
        "ryan":     "am_michael",   # us male, neutral
        "lessac":   "af_heart",     # us female, warm
        "joe":      "am_adam",      # us male, deep
        "amy":      "af_sarah",     # us female, bright
        "kathleen": "af_nova",      # us female, expressive
        "kusal":    "am_eric",      # us male, casual
        "northern": "bm_lewis",     # british male, alt
        "southern": "bf_emma",      # british female, posh
        "jenny":    "bf_isabella",  # british female, friendly
    }

    # Shared model instance — Kokoro is one network, voices are embeddings.
    _shared_kokoro = None
    _shared_lock = threading.Lock()

    def __init__(self, voice_name):
        with KokoroSynth._shared_lock:
            if KokoroSynth._shared_kokoro is None:
                from kokoro_onnx import Kokoro
                model_path = os.path.join("models", "kokoro", "kokoro-v1.0.onnx")
                voices_path = os.path.join("models", "kokoro", "voices-v1.0.bin")
                if not os.path.exists(model_path) or not os.path.exists(voices_path):
                    raise FileNotFoundError(
                        "Kokoro model files missing. Re-run setup.bat and "
                        "choose to install Kokoro support."
                    )
                KokoroSynth._shared_kokoro = Kokoro(model_path, voices_path)
            self._kokoro = KokoroSynth._shared_kokoro
        self.voice_name = voice_name
        self.kokoro_voice = self.VOICE_MAP.get(voice_name, "af_heart")
        self.sample_rate = 24000  # Kokoro outputs 24 kHz

    def synthesize(self, text):
        samples, sr = self._kokoro.create(
            text, voice=self.kokoro_voice, speed=1.0, lang="en-us",
        )
        # kokoro-onnx returns float32 already in [-1, 1]
        if samples.size == 0:
            return np.zeros(0, dtype=np.float32), sr
        return samples.astype(np.float32), sr


class Pyttsx3Synth:
    """Fallback synthesizer using pyttsx3 (SAPI on Windows)."""

    name = "pyttsx3"
    sample_rate = 22050

    def __init__(self):
        import pyttsx3
        self._pyttsx3 = pyttsx3
        self._lock = threading.Lock()

    def synthesize(self, text):
        # Render to a temp wav, read it back.
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with self._lock:
                eng = self._pyttsx3.init()
                try:
                    eng.save_to_file(text, path)
                    eng.runAndWait()
                finally:
                    try: eng.stop()
                    except Exception: pass
                    del eng
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                return np.zeros(0, dtype=np.float32), self.sample_rate
            with wave.open(path, "rb") as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                raw = wf.readframes(wf.getnframes())
            dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(sw)
            if dtype is None:
                return np.zeros(0, dtype=np.float32), sr
            arr = np.frombuffer(raw, dtype=dtype).astype(np.float32)
            arr /= float(np.iinfo(dtype).max)
            if ch > 1:
                arr = arr.reshape(-1, ch).mean(axis=1)
            return arr, sr
        finally:
            try: os.remove(path)
            except OSError: pass


def _make_synth(voice_name, logger, engine="piper"):
    """
    Build a synth for `voice_name` using the chosen engine.
    Falls back through: requested engine -> Piper -> pyttsx3.
    """
    engine = (engine or "piper").lower()

    if engine == "kokoro":
        try:
            synth = KokoroSynth(voice_name)
            logger(f"[TTS] Kokoro voice loaded: {voice_name} "
                   f"-> {synth.kokoro_voice}")
            return synth
        except FileNotFoundError as e:
            logger(f"[TTS] {e}")
        except ImportError:
            logger("[TTS] kokoro-onnx not installed. Re-run setup.bat "
                   "and enable Kokoro support.")
        except Exception as e:
            logger(f"[TTS] Kokoro init failed ({e}).")
        logger("[TTS] Falling back to Piper.")

    try:
        synth = PiperSynth(voice_name)
        logger(f"[TTS] Piper voice loaded: {voice_name}")
        return synth
    except FileNotFoundError as e:
        logger(f"[TTS] {e}")
    except ImportError:
        logger("[TTS] piper-tts not installed.")
    except Exception as e:
        logger(f"[TTS] Piper init failed ({e}).")
    logger("[TTS] Falling back to pyttsx3.")
    try:
        return Pyttsx3Synth()
    except Exception as e:
        logger(f"[TTS] pyttsx3 also failed: {e}")
        return None


# --- Public API -------------------------------------------------------------

class VoiceFX:
    """
    Queues TTS requests, synthesizes, applies DSP, plays.

    Holds a pool of synths keyed by voice name and lazy-loads each on
    first use, so the per-department voice routing only pays a one-time
    cost per voice (~50ms each).
    """

    def __init__(self, voice="alan", style="radio", enabled=True, logger=None,
                 engine="piper"):
        self._logger = logger or (lambda m: None)
        self.style = (style or "clean").lower()
        self.enabled = bool(enabled)
        self.default_voice = voice
        self.engine = (engine or "piper").lower()
        self._synths = {}
        self._fallback = None  # only initialized if Piper is missing

        # Warm up the default voice so the first line isn't delayed
        self._get_synth(voice)

        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # --- synth pool ---
    def _get_synth(self, voice_name):
        """Return a synth for `voice_name`, loading it if necessary."""
        if not voice_name:
            voice_name = self.default_voice
        if voice_name in self._synths:
            return self._synths[voice_name]
        # Build via the engine-aware factory.
        synth = _make_synth(voice_name, self._logger, engine=self.engine)
        if synth is not None:
            self._synths[voice_name] = synth
            return synth
        # Fallback: reuse default if loaded, else pyttsx3
        if self.default_voice in self._synths:
            return self._synths[self.default_voice]
        if self._fallback is None:
            try:
                self._fallback = Pyttsx3Synth()
            except Exception:
                pass
        return self._fallback

    def set_default_voice(self, voice_name):
        """Change the default voice (used when no per-command voice given)."""
        if not voice_name:
            return False
        synth = self._get_synth(voice_name)
        if synth is None:
            return False
        self.default_voice = voice_name
        return True

    # backwards-compat alias used by the GUI
    def set_voice(self, voice_name):
        return self.set_default_voice(voice_name)

    def set_style(self, style):
        self.style = (style or "clean").lower()

    def speak(self, text, voice=None):
        """Queue text for speech. `voice` overrides the default per-call."""
        if not self.enabled or not text:
            return
        self._queue.put(("speak", text, voice))

    def run_after(self, callback):
        """
        Queue `callback` to run after every currently-queued line finishes
        playing. Used to fire keybinds AFTER the crew member has finished
        speaking, so the relay sequence reads cleanly in-game.
        Falls back to running immediately if TTS is disabled.
        """
        if not callable(callback):
            return
        if not self.enabled:
            try: callback()
            except Exception: pass
            return
        self._queue.put(("call", callback, None))

    def shutdown(self):
        self._stop.set()
        self._queue.put(None)

    def warmup(self, priority_voices, background_voices=None):
        """
        Load voices in two phases on a background thread:
          1. priority_voices  — loaded first, sequentially. Use for the
             active crew so the first ATC/tactical/etc reply has no hitch.
          2. background_voices — loaded after, also sequentially. Use for
             the rest of the voice catalogue so by ~2 minutes everything
             is warm and the GUI Voice-test buttons feel instant.

        Each Piper model takes ~2s to load when the system is idle, ~10s
        when SC (or similar) is contending for disk + CPU.

        Parallel loading does NOT help: ONNX Runtime serializes
        InferenceSession initialization under a global lock, so spawning
        threads gives identical wall-clock to sequential. For the real
        speed win, build the sound_cache so common commands skip Piper
        entirely at play time.

        Safe to call more than once; voices already cached are skipped.
        """
        priority = [v for v in (priority_voices or []) if v]
        background = [v for v in (background_voices or []) if v
                      and v not in priority]

        def _run():
            if priority:
                self._logger(
                    f"[TTS] Warming {len(priority)} crew voice(s) "
                    f"(~{len(priority) * 10}s)..."
                )
                for v in priority:
                    if v in self._synths:
                        continue
                    self._logger(f"[TTS] Warming {v} (crew)...")
                    self._get_synth(v)
                self._logger("[TTS] Crew warm. Ready.")
            if background:
                self._logger(
                    f"[TTS] Drifting in {len(background)} extra voice(s) "
                    f"in the background..."
                )
                for v in background:
                    if v in self._synths:
                        continue
                    self._logger(f"[TTS] Warming {v} (background)...")
                    self._get_synth(v)
                self._logger("[TTS] All voices loaded.")
        threading.Thread(target=_run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                break
            kind, a, b = item
            try:
                if kind == "speak":
                    self._speak_now(a, b)
                elif kind == "call":
                    a()  # callback
            except Exception as e:
                self._logger(f"[TTS] queue item failed: {e}")

    def _speak_now(self, text, voice):
        synth = self._get_synth(voice or self.default_voice)
        if synth is None:
            return
        samples, sr = synth.synthesize(text)
        if samples.size == 0:
            return
        if self.style == "radio":
            samples = _apply_radio(samples, sr)
        elif self.style == "robot":
            samples = _apply_robot(samples, sr)
        sd.play(samples.astype(np.float32), sr)
        sd.wait()
