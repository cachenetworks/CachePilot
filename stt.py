"""
Speech-to-text engines for CachePilot.

Two backends:
  - "whisper": local faster-whisper. Offline, more accurate, but cold-loads
    a model (~200MB-1GB depending on size) on first use.
  - "google":  online Google Web Speech via SpeechRecognition. Free,
    fast, no install footprint, but requires internet and is rate-limited.

The runtime picks the engine based on config; if Whisper fails to load
we fall back to Google with a warning.
"""

import os
import io
import threading

import speech_recognition as sr


# Project-local model cache so users don't end up with random folders
# in %USERPROFILE%\.cache.
MODEL_DIR = os.path.join("models", "whisper")


class GoogleSTT:
    """Online STT via SpeechRecognition.recognize_google."""

    name = "google"

    def transcribe(self, audio, recognizer):
        try:
            return recognizer.recognize_google(audio)
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            raise RuntimeError(f"Google STT request failed: {e}")


class WhisperSTT:
    """Offline STT via faster-whisper."""

    name = "whisper"

    def __init__(self, model_size="base", device="cpu", compute_type="int8",
                 language="en", logger=None):
        from faster_whisper import WhisperModel
        os.makedirs(MODEL_DIR, exist_ok=True)
        self._model_size = model_size
        self._language = language
        self._logger = logger or (lambda m: None)
        # Loading the model can take 5-30s on first call as the weights
        # download to MODEL_DIR. After that it's fast.
        self._logger(f"[STT] Loading faster-whisper {model_size} on {device} "
                     f"({compute_type})...")
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=MODEL_DIR,
        )
        self._logger(f"[STT] faster-whisper {model_size} ready.")

    def transcribe(self, audio, recognizer):
        """
        `audio` is a SpeechRecognition AudioData. We extract its raw WAV
        bytes, hand them to faster-whisper as a BytesIO, and return the
        concatenated transcript.
        """
        wav_bytes = audio.get_wav_data(convert_rate=16000, convert_width=2)
        bio = io.BytesIO(wav_bytes)
        # beam_size 1 + greedy = fast on CPU; ~95% as good as beam 5 for
        # short utterances.
        segments, _info = self.model.transcribe(
            bio,
            language=self._language,
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            no_speech_threshold=0.6,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text or None


# --- factory ---------------------------------------------------------------

# Cache the active engine so we don't reload Whisper every time.
_engine = None
_engine_lock = threading.Lock()


def get_engine(name, model_size="base", device="cpu", logger=None):
    """
    Return an STT engine instance.

    `name` is "whisper" or "google". If Whisper fails to import or load
    we fall back to Google and log a warning.
    """
    global _engine
    log = logger or (lambda m: None)
    name = (name or "google").lower()

    with _engine_lock:
        # Cache hit
        if _engine is not None and getattr(_engine, "name", None) == name:
            if name == "whisper" and getattr(_engine, "_model_size", None) != model_size:
                pass  # need to reload below
            else:
                return _engine

        if name == "whisper":
            try:
                _engine = WhisperSTT(
                    model_size=model_size, device=device,
                    compute_type="int8", logger=log,
                )
                return _engine
            except ImportError:
                log("[STT] faster-whisper not installed; using Google.")
            except Exception as e:
                log(f"[STT] Whisper init failed ({e}); using Google.")

        _engine = GoogleSTT()
        log("[STT] Using Google online STT.")
        return _engine


def warmup_whisper(model_size="base", device="cpu", logger=None):
    """Trigger Whisper download/load on a background thread."""
    def _run():
        get_engine("whisper", model_size=model_size, device=device, logger=logger)
    threading.Thread(target=_run, daemon=True).start()
