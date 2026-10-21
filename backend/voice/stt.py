"""
Speech → Text. Apple-Silicon-first, CPU fallback.

SECURITY: Both engines run 100% on-device. Audio never leaves the Mac.
The model files are downloaded once at install time from known sources
(HuggingFace); at runtime there are zero network calls.
"""
import numpy as np

_engine = None
_engine_name = None

# whisper-tiny often mishears a lone "Jarvis" ("Jovis", "Darvas", "Chavez"),
# so the wake word never matched. A short prompt biases it toward the name
# (and app names) without inventing words in silence or noise.
VOCAB_PROMPT = "Jarvis, open Finder. Jarvis, what time is it?"


def _vocab_prompt():
    """Base prompt + the user's own apps (Granola, Slack…) so Whisper spells
    them right. Only /Applications — system apps are common words already."""
    from pathlib import Path
    try:
        apps = sorted(p.stem for p in Path("/Applications").glob("*.app"))[:40]
    except OSError:
        apps = []
    return VOCAB_PROMPT + (" Apps: " + ", ".join(apps) + "." if apps else "")


_prompt = None


def _load_mlx():
    """Apple-Silicon optimized whisper. Preferred on M-series Macs."""
    global _engine, _engine_name
    try:
        import mlx_whisper
        _engine = mlx_whisper
        _engine_name = "mlx-whisper"
        return True
    except ImportError:
        return False


def _load_faster():
    """CPU fallback for Intel Macs or if mlx fails."""
    global _engine, _engine_name
    try:
        from faster_whisper import WhisperModel
        # SECURITY: tiny.en is English-only. A multilingual model could
        # mis-transcribe; the English-only model is smaller AND safer
        # for an English-first assistant.
        _engine = WhisperModel("tiny.en", device="cpu", compute_type="int8")
        _engine_name = "faster-whisper"
        return True
    except ImportError:
        return False


def transcribe(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribe float32 mono audio to English text.

    SECURITY: audio is processed in-memory and discarded after.
    We never write recordings to disk — no audio files means
    nothing to leak later.
    """
    global _engine, _prompt
    if _prompt is None:
        _prompt = _vocab_prompt()
    if _engine is None:
        # Prefer Apple-Silicon path; fall back to CPU whisper.
        if not _load_mlx() and not _load_faster():
            raise RuntimeError(
                "No STT engine installed. Run install.sh first."
            )

    # SECURITY: Clamp input length. A corrupt/oversized array must not
    # hang the model — 30s at 16kHz is the hard ceiling (matches loop.py).
    max_len = sample_rate * 30
    audio = np.asarray(audio, dtype=np.float32)[:max_len]

    if _engine_name == "mlx-whisper":
        result = _engine.transcribe(audio, path_or_hf_repo="mlx-community/whisper-tiny.en-mlx",
                                    initial_prompt=_prompt, condition_on_previous_text=False)
        text = result.get("text", "")
    else:
        segments, _ = _engine.transcribe(audio, initial_prompt=_prompt,
                                         condition_on_previous_text=False)
        text = " ".join(s.text for s in segments)

    # SECURITY: Strip control characters. Transcription output feeds
    # into the TTS engine and (in F2) the LLM — control chars could
    # inject terminal escape sequences or prompt-injection payloads.
    text = "".join(c for c in text.strip() if c.isprintable() or c == " ")
    return " ".join(text.split())  # collapse whitespace
