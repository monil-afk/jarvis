"""
Text → Speech via macOS built-in `say`. British voice, film-JARVIS register.

SECURITY: `say` is a system binary — no network, no account, no audio
upload. We invoke it with an argument list (never shell=True), so text
content cannot inject shell commands.
"""
import shutil
import subprocess

# Magnus-equivalent on macOS: Daniel (British English, deep, measured).
# SECURITY: Voice name is allow-listed below. A caller passing an
# arbitrary voice string could probe installed voices; we don't allow it.
ALLOWED_VOICES = {"Daniel", "Arthur", "George"}
DEFAULT_VOICE = "Daniel"
# Words-per-minute. Film JARVIS speaks deliberately; 170 is calm but not slow.
DEFAULT_RATE = 170
# SECURITY: Cap utterance length. A runaway LLM (F2+) must not be able
# to make JARVIS talk for an hour straight.
MAX_CHARS = 500


def speak(text: str, voice: str = DEFAULT_VOICE, rate: int = DEFAULT_RATE):
    """Speak text aloud. Blocks until finished.

    SECURITY:
    - `say` path resolved via shutil.which — never a hardcoded path that
      could be shadowed, never shell=True (no injection from text).
    - Text truncated to MAX_CHARS and stripped of control chars.
    - Voice restricted to ALLOWED_VOICES.
    """
    say_bin = shutil.which("say")
    if not say_bin:
        raise RuntimeError("macOS `say` not found — not running on macOS?")

    if voice not in ALLOWED_VOICES:
        voice = DEFAULT_VOICE
    # Clamp rate to sane bounds (a bad value shouldn't chipmunk/drone).
    rate = max(100, min(250, int(rate)))

    clean = "".join(c for c in text[:MAX_CHARS] if c.isprintable() or c == " ")
    clean = " ".join(clean.split())
    if not clean:
        return

    # SECURITY: argument list, NOT a shell string. Even if `clean`
    # contained `; rm -rf ~`, it would be spoken literally, not executed.
    r = subprocess.run(
        [say_bin, "-v", voice, "-r", str(rate), clean],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if r.returncode != 0:
        # Voice not installed on this Mac — speak in the system voice
        # rather than staying silent.
        subprocess.run([say_bin, "-r", str(rate), clean], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
