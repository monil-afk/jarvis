"""
Wake-word detection — listens for "(hey) jarvis" using openWakeWord.

SECURITY: Runs fully on-device. Audio frames are processed in small
chunks and never stored, transmitted, or logged. The mic stream exists
only inside this process's memory.

WHY ONNX + EXPLICIT MODEL FILES (v1.0.5 fix):
- openWakeWord defaults to tflite, and tflite-runtime has no wheels for
  macOS ARM64 + Python 3.12. So we always use ONNX (onnxruntime is in
  requirements.txt). Do NOT add tflite-runtime.
- The pip package does NOT ship the model files. Without them, even
  inference_framework="onnx" crashes with NO_SUCHFILE. We fetch the three
  small ONNX files (~3.7 MB total) ONCE from openWakeWord's official GitHub
  release into ~/.jarvis/models, then run 100% offline forever after.
"""
import os
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import sounddevice as sd

# openWakeWord is imported lazily so the module loads even before install.
try:
    from openwakeword.model import Model as WakeModel
    _HAS_OWW = True
except ImportError:
    _HAS_OWW = False

SAMPLE_RATE = 16000
FRAME_SIZE = 1280  # 80ms frames — what openWakeWord expects

# SECURITY: Fixed, official source (openWakeWord's own GitHub release).
# Downloaded once, then cached. No user input ever becomes a URL here.
_RELEASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
MODEL_FILES = {
    "melspec": "melspectrogram.onnx",
    "embedding": "embedding_model.onnx",
    "jarvis": "hey_jarvis_v0.1.onnx",
}
MODELS_DIR = Path.home() / ".jarvis" / "models"
# A real model file is ~1 MB. Anything tiny is an error page, not a model.
_MIN_MODEL_BYTES = 100_000


def _package_models_dir():
    """Where `openwakeword.utils.download_models()` would have put files."""
    try:
        import openwakeword
        return Path(openwakeword.__file__).parent / "resources" / "models"
    except Exception:
        return None


def ensure_models() -> dict:
    """Make sure the three ONNX model files exist locally. Returns paths.

    Order: ~/.jarvis/models → openWakeWord's own resources dir → download.
    SECURITY: downloads go to a .part file and are renamed only after a
    size sanity check, so a failed/partial download is never loaded.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    pkg_dir = _package_models_dir()
    paths = {}
    for key, fname in MODEL_FILES.items():
        target = MODELS_DIR / fname
        if not (target.exists() and target.stat().st_size > _MIN_MODEL_BYTES):
            src = pkg_dir / fname if pkg_dir else None
            if src is not None and src.exists() and src.stat().st_size > _MIN_MODEL_BYTES:
                target.write_bytes(src.read_bytes())
            else:
                print(f"[wake] downloading {fname} (one time)...", flush=True)
                part = target.with_suffix(".part")
                with urllib.request.urlopen(_RELEASE + fname, timeout=60) as r:
                    data = r.read()
                if len(data) < _MIN_MODEL_BYTES:
                    raise RuntimeError(f"Download of {fname} looks broken ({len(data)} bytes).")
                part.write_bytes(data)
                os.replace(part, target)
        paths[key] = str(target)
    return paths


class WakeWordDetector:
    def __init__(self, keyword="jarvis", threshold=0.5, on_level=None):
        """
        keyword:   kept for compatibility; the model is "hey jarvis", which
                   also fires on a clear "jarvis".
        threshold: 0..1 score needed to trigger. Lower = more sensitive.
        on_level:  optional callback(level_0_to_100) for the HUD mic meter.
        """
        # SECURITY: Fail loudly if the wake-word engine is missing.
        # Silently falling back to "always listening" would record
        # everything — the opposite of what we want.
        if not _HAS_OWW:
            raise RuntimeError("openWakeWord not installed. Run install.sh first.")
        self.keyword = keyword
        self.threshold = threshold
        self.on_level = on_level
        paths = ensure_models()
        # SECURITY: model files are loaded from local disk only.
        self.model = WakeModel(
            wakeword_models=[paths["jarvis"]],
            inference_framework="onnx",
            melspec_model_path=paths["melspec"],
            embedding_model_path=paths["embedding"],
        )
        self._heard = False
        self._last_level_ts = 0.0

    def _audio_callback(self, indata, frames, time_info, status):
        # SECURITY: indata is a borrowed buffer — we copy nothing out.
        # Only the model's score (and a mic level number) leaves here.
        audio = indata[:, 0]
        scores = self.model.predict(audio)
        # The score key is the model's file name (e.g. "hey_jarvis_v0.1"),
        # not "jarvis" — so take the best score of whatever is loaded.
        if scores and max(scores.values()) >= self.threshold:
            self._heard = True
        if self.on_level:
            now = time.time()
            if now - self._last_level_ts > 0.3:
                self._last_level_ts = now
                rms = float(np.sqrt(np.mean((audio.astype(np.float32) / 32768.0) ** 2)))
                self.on_level(min(100.0, rms * 400.0))

    def wait_for_wake(self):
        """Block until the wake word is heard."""
        self._heard = False
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            dtype="int16", blocksize=FRAME_SIZE,
                            callback=self._audio_callback):
            while not self._heard:
                sd.sleep(50)
        # Clear the model's rolling buffers so the same "jarvis" can't
        # re-trigger the moment we start listening again.
        self.model.reset()

    def close(self):
        # openWakeWord holds no external resources; nothing to release.
        pass


if __name__ == "__main__":
    # `python -m backend.voice.wake` — fetch models and test detection.
    print("Model files:", ensure_models())
    d = WakeWordDetector()
    print("Say 'hey jarvis'...")
    d.wait_for_wake()
    print("Heard it!")
    sys.exit(0)
