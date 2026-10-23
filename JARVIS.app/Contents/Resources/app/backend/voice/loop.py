"""
JARVIS Voice Loop — live conversation.

  "Jarvis, open Finder"          → acts at once (one breath, no "Yes, sir" wait)
  "Jarvis" … "open Finder"       → quick chime, then it listens
  after every reply              → keeps listening for follow-ups (no wake word)
  "that's all" / silence         → back to standby

How the wake word works (default, wake_engine: whisper): JARVIS listens for
speech, transcribes each short utterance LOCALLY with Whisper and only acts
when it starts with "Jarvis". Nothing is stored or sent anywhere; utterances
without the wake word are discarded immediately. (wake_engine: openwakeword
keeps the older, lower-CPU "Hey Jarvis" model.)

Every step reports its state to the HUD server (idle / listening /
thinking / speaking) through backend/state_client.py.

SECURITY NOTE: This module touches the microphone and speaker, and hands
TEXT ONLY (never audio) to the brain. All actions go through the brain's
security gates (core/security.py): destructive skills are confirmed aloud
first, and every decision is written to the audit log.
"""
import fcntl
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from ..state_client import report
from .stt import transcribe
from .tts import speak
from .wake import WakeWordDetector

# SECURITY: Fixed audio parameters. 16kHz mono is all whisper needs.
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK = 1024
# SECURITY: Cap recording length. A stuck mic must never fill RAM —
# 30 seconds of 16kHz float32 mono is ~2MB, a safe ceiling.
MAX_RECORD_SECONDS = 30
# After the wake word: wait up to this long for you to START talking...
SPEECH_START_TIMEOUT = 6.0
# ...then stop on this much silence.
# Minimum loudness that counts as speech. The real bar adapts to the room:
# 3x the background noise level. (A fixed 0.015 missed normal speech when
# macOS mic input volume was low — e.g. after Zoom turned it down.)
SILENCE_THRESHOLD = 0.006
_NOISE = {"floor": 0.003, "hist": []}   # background noise, learned continuously
_NOISE_WINDOW = 80          # ~5 s of 1024-sample blocks
_NOISE_PCT = 20             # quietest 20% of recent sound = the room's noise
# Mic health, for diagnosing "it doesn't hear me": loudest level seen and
# whether the stream is pure digital silence (= macOS denied the mic).
_MIC = {"peak": 0.0, "nonzero": False, "since": 0.0}
# Widget shows "YOU" while you're talking; stays on this long through pauses.
USER_TALK_HANG_S = 0.45
WAKE_ACK = "Yes, sir."


def _voice_config():
    try:
        import yaml
        cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / "jarvis.yaml"
        with open(cfg_path) as f:
            return (yaml.safe_load(f) or {}).get("voice") or {}
    except Exception:
        return {}


_VCFG = _voice_config()
WAKE_THRESHOLD = float(os.environ.get("JARVIS_WAKE_THRESHOLD", _VCFG.get("wake_sensitivity", 0.5)))
WAKE_ENGINE = str(_VCFG.get("wake_engine", "whisper")).lower()
# How long a pause means "I've finished talking". Shorter = snappier.
SILENCE_SECONDS = float(_VCFG.get("end_of_speech_seconds", 0.8))
# Standby: longest utterance we'll check for the wake word.
# Kept short so that in a noisy room (where sound never "stops") a "Jarvis"
# is still transcribed within a few seconds.
STANDBY_MAX_SECONDS = 8
PREROLL_SECONDS = 0.4   # audio kept from just before speech starts

# "Jarvis" and how Whisper sometimes spells it — ANYWHERE in what was heard.
# In a noisy room (a call, music, people talking) background sound and your
# "Jarvis, …" end up in one chunk, so the name is rarely the first word.
_WAKE = re.compile(r"\b(?:jarvis|jervis|javis|jarvas|jarviss|jarvi|jarvice|jarbis|jarvus|jovis|jarvees|"
                   r"jarves|darvis|darvas)\b[\s,.!?:;'-]*", re.I)


def split_wake(text):
    """("Jarvis, open Finder.") -> (True, "open Finder.");  ("lunch?") -> (False, "").
    ("…so anyway. Jarvis, what's next?") -> (True, "what's next?") — the
    command is whatever follows the LAST "Jarvis"."""
    text = text or ""
    # Addressed, not mentioned: "Jarvis" starts a sentence ("… fine. Jarvis what's
    # next") or is followed by a comma ("…so Jarvis, open Slack") — but not
    # "I told Jarvis yesterday".
    hits = [m for m in _WAKE.finditer(text)
            if re.fullmatch(r"(?:.*[.!?])?\W*(?:(?:hey|hi|ok|okay|yo|um|uh|so|and|oh)\W+){0,2}",
                            text[:m.start()], re.I | re.S)
            or "," in m.group(0) or not text[m.end():].strip()]
    if not hits:
        return False, ""
    rest = text[hits[-1].end():].strip()
    if re.sub(r"[^\w]", "", rest) == "":
        rest = ""
    return True, rest
# After JARVIS answers, keep listening this long for a follow-up — no
# wake word needed. 0 turns conversation mode off.
FOLLOW_UP_SECONDS = float(_VCFG.get("follow_up_seconds", 6))
CHIMES = bool(_VCFG.get("chimes", True))
# Saying one of these ends the conversation (back to waiting for "Jarvis").
_END_PHRASES = re.compile(r"^(that'?s all|that is all|that'?s it|that is it|(?:ok(?:ay)? )?(?:thanks?|thank you)(?: jarvis)?$|that will be all|that'?ll be all|never ?mind|nothing|"
                          r"stop|stop listening|go to standby|standby|goodbye|bye|no thanks|no thank you|"
                          r"i'?m good|i'?m done|we'?re done)\b", re.I)

# macOS built-in sounds: a soft "tink" = I'm listening, "pop" = got it.
_SOUNDS = {"start": "/System/Library/Sounds/Tink.aiff", "stop": "/System/Library/Sounds/Pop.aiff"}


def chime(kind):
    """Play a short cue so you always know when JARVIS is listening.
    The start chime blocks (~0.1s) so it isn't recorded as your speech."""
    path = _SOUNDS.get(kind)
    if not (CHIMES and path and os.path.exists(path)):
        return
    try:
        if kind == "start":
            subprocess.run(["afplay", path], check=False, timeout=3)
        else:
            subprocess.Popen(["afplay", path])
    except Exception:
        pass

# Whisper "hears" these in pure silence/noise. Never act on them.
_HALLUCINATIONS = {"", "you", "thank you", "thanks", "thank you very much",
                   "thanks for watching", "thank you for watching", "bye", "okay", "ok"}

_LOCK_PATH = Path.home() / ".jarvis" / "voice.lock"
_lock_file = None


def log(msg, level="info", hud=True):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    if hud:
        report(log=msg, log_level=level)


def _single_instance():
    """Make sure only ONE voice loop runs. Two loops = double replies.

    If an older loop is still alive (e.g. from a crashed launch), stop it.
    """
    global _lock_file
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _lock_file = open(_LOCK_PATH, "a+")
    for _ in range(20):
        try:
            fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            _lock_file.seek(0)
            pid = _lock_file.read().strip()
            if pid.isdigit() and int(pid) != os.getpid():
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            time.sleep(0.25)
    else:
        print("Another JARVIS voice loop is running; exiting.", flush=True)
        sys.exit(0)
    _lock_file.seek(0)
    _lock_file.truncate()
    _lock_file.write(str(os.getpid()))
    _lock_file.flush()


def _reset_audio():
    """Re-initialise PortAudio so it sees the current default microphone."""
    try:
        sd._terminate()
    except Exception:
        pass
    try:
        sd._initialize()
        dev = sd.query_devices(kind="input")
        print(f"[mic] audio reset — using {dev['name']}", flush=True)
    except Exception as e:
        print(f"[mic] audio reset failed: {e}", flush=True)


def _input_volume():
    try:
        out = subprocess.run(["osascript", "-e", "input volume of (get volume settings)"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None


def _watch_input_volume():
    """Meeting apps (Zoom, Meet…) often turn the Mac's mic input down, and
    then JARVIS can't hear you. Say so instead of failing silently."""
    warned = False
    while True:
        vol = _input_volume()
        if vol is not None and vol < 40:
            if not warned:
                log(f"Mic input volume is only {vol}% — I may not hear you. "
                    "Raise it in System Settings › Sound › Input.", "warn")
                warned = True
        else:
            warned = False
        time.sleep(60)


def _mic_monitor():
    """Every 10s write the mic level to the console log (never words)."""
    silent_checks = 0
    while True:
        time.sleep(10)
        peak, nonzero = _MIC["peak"], _MIC["nonzero"]
        _MIC.update(peak=0.0, nonzero=False)
        thresh = max(SILENCE_THRESHOLD, _NOISE["floor"] * 2.5)
        print(f"[mic] loudest {peak:.4f}  noise {_NOISE['floor']:.4f}  speech bar {thresh:.4f}"
              f"{'' if nonzero else '  (PURE SILENCE)'}", flush=True)
        silent_checks = 0 if nonzero else silent_checks + 1
        if silent_checks == 2:
            log("The microphone is sending pure silence — macOS has probably blocked it. "
                "Open System Settings › Privacy & Security › Microphone and switch JARVIS on.", "warn")


def _heartbeat():
    """Tell the HUD we're alive every few seconds (even mid-thought)."""
    while True:
        report(voice_status="online")
        time.sleep(4)


def _capture(start_timeout=SPEECH_START_TIMEOUT, max_seconds=MAX_RECORD_SECONDS, live_ui=True):
    """Record one utterance: from when you start talking until you pause.

    start_timeout: give up if nobody starts talking within this many seconds
                   (None = wait as long as it takes — standby mode).
    live_ui:       report "you are speaking" + mic level to the widget.
                   Off in standby, so the widget stays calm until it's
                   actually spoken to.
    Returns float32 audio, or None if no speech was heard.
    SECURITY: Bounded by max_seconds so a malfunction or adversarial noise
    cannot exhaust memory. In standby nothing is kept beyond one utterance.
    """
    frames = []
    pre = []                                   # rolling pre-roll before speech
    pre_max = max(1, int(PREROLL_SECONDS * SAMPLE_RATE / BLOCK))
    st = {"speech": False, "silent": 0, "last_lvl": 0.0, "talking": False, "last_loud": 0.0,
          "floor": _NOISE["floor"], "n": 0}
    silent_needed = max(1, int(SILENCE_SECONDS * SAMPLE_RATE / BLOCK))

    def callback(indata, frame_count, time_info, status):
        # SECURITY: Copy the buffer immediately. sounddevice reuses the
        # underlying memory; holding a reference would corrupt audio.
        block = indata.copy()
        rms = float(np.sqrt(np.mean(block ** 2)))
        _MIC["peak"] = max(_MIC["peak"], rms)
        if rms > 0:
            _MIC["nonzero"] = True
        now = time.time()
        # Noise floor = quietest 20% of the last ~5 s, updated on EVERY block,
        # so a room with constant background sound raises the bar instead of
        # everything counting as speech.
        hist = _NOISE["hist"]
        hist.append(rms)
        del hist[:-_NOISE_WINDOW]
        if len(hist) >= 16 and st["n"] % 4 == 0:
            _NOISE["floor"] = st["floor"] = float(np.percentile(hist, _NOISE_PCT))
        st["n"] += 1
        thresh = max(SILENCE_THRESHOLD, st["floor"] * 2.5)
        loud = rms >= thresh
        if not st["speech"]:
            pre.append(block)
            del pre[:-pre_max]
            if loud:
                st["speech"] = True
                frames.extend(pre)
        else:
            frames.append(block)
        if loud:
            st["silent"] = 0
            st["last_loud"] = now
        else:
            st["silent"] += 1
        if live_ui:
            # "You are speaking" for the widget: on as soon as you're loud,
            # off after a short pause (so it doesn't flicker between words).
            talking = (now - st["last_loud"]) < USER_TALK_HANG_S
            if talking != st["talking"]:
                st["talking"] = talking
                report(user_talking=talking, mic=min(100.0, rms * 400.0))
            if now - st["last_lvl"] > 0.12:
                st["last_lvl"] = now
                report(mic=min(100.0, rms * 400.0))

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                        dtype="float32", blocksize=BLOCK, callback=callback):
        start = time.time()
        speech_start = None
        while True:
            now = time.time()
            if st["speech"] and speech_start is None:
                speech_start = now
            if not st["speech"] and start_timeout is not None and now - start > start_timeout:
                break  # nobody spoke
            if st["speech"] and st["silent"] >= silent_needed:
                break  # finished speaking
            if speech_start is not None and now - speech_start > max_seconds:
                break
            time.sleep(0.03)
    if live_ui:
        report(mic=0, user_talking=False)

    if not frames or not st["speech"]:
        return None
    audio = np.concatenate(frames, axis=0).flatten()
    # Normalize to [-1, 1] so quiet speakers transcribe as well as loud ones.
    peak = float(np.max(np.abs(audio)))
    if peak > 0:
        audio = audio / peak
    return audio


def _record_until_silence(start_timeout=SPEECH_START_TIMEOUT):
    return _capture(start_timeout=start_timeout)


def _clean_transcript(text):
    if re.sub(r"[^\w\s]", "", (text or "").lower()).strip() in _HALLUCINATIONS:
        return ""
    return (text or "").strip()


def _listen_once(start_timeout=SPEECH_START_TIMEOUT):
    """Record + transcribe one utterance. Returns text ('' if nothing)."""
    report(state="listening", transcript="", task="")
    chime("start")
    audio = _capture(start_timeout)
    if audio is None or len(audio) < int(SAMPLE_RATE * 0.35):
        # SECURITY: Ignore blips (a cough or mic pop).
        return ""
    chime("stop")
    report(state="thinking")
    return _clean_transcript(transcribe(audio))


def _standby_utterance():
    """Standby: wait for any speech, transcribe it locally, and return the
    command if it was addressed to Jarvis. (None = not for us.)
    Returns "" for a bare "Jarvis" (wake word with no command)."""
    audio = _capture(start_timeout=None, max_seconds=STANDBY_MAX_SECONDS, live_ui=False)
    if audio is None or len(audio) < int(SAMPLE_RATE * 0.3):
        return None
    text = _clean_transcript(transcribe(audio))
    woke, command = split_wake(text)
    if not woke:
        # SECURITY/PRIVACY: not for JARVIS — discarded. Only the length is
        # logged (to the console), never the words.
        print(f"[standby] heard {len(audio) / SAMPLE_RATE:.1f}s of speech, no wake word", flush=True)
        return None
    log(f"You: {text}")
    return command


def say(text):
    """Speak with the HUD showing 'speaking'."""
    if not text:
        return
    report(state="speaking", reply=text)
    speak(text)


_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|do it|go ahead|proceed|confirm|confirmed|"
                  r"affirmative|please do|correct|ok|okay)\b", re.I)
_NO = re.compile(r"\b(no|nope|don'?t|do not|cancel|stop|abort|negative|never ?mind)\b", re.I)


def ask_user(prompt: str) -> bool:
    """Security confirmation: ask aloud, listen for yes/no.

    SECURITY: anything other than a clear "yes" (silence, mumbling, "no")
    counts as NO. Fails closed.
    """
    report(activity="Waiting for your OK", activity_status="working")
    say(prompt)
    answer = _listen_once()
    log(f"Confirmation answer: {answer or '(nothing)'}")
    approved = bool(answer) and bool(_YES.search(answer)) and not _NO.search(answer)
    return approved


def _activity(text, status="working"):
    """What JARVIS is doing right now, e.g. ("Opening Finder", "working")
    then ("Opened Finder.", "done"). Shown live on the widget."""
    if status == "working" and text and text != "Thinking it over":
        report(state="working", activity=text, activity_status=status, task=text)
    else:
        report(activity=text, activity_status=status, task=text)
    if text and status in ("done", "failed"):
        log(("✓ " if status == "done" else "✗ ") + text, "ok" if status == "done" else "warn")


def handle_command(text, skills):
    """The brain step: text in → skill runs (via security gate) → reply spoken."""
    from ..core import brain
    brain.set_activity_hook(_activity)
    report(state="thinking", transcript=text, reply="", task="")
    try:
        reply = brain.think(text, skills, ask_user)
    except Exception as e:  # never let one bad command kill the loop
        log(f"Brain error: {type(e).__name__}: {e}", "warn")
        reply = "Something went wrong there, sir."
    log(f"JARVIS: {reply}", "ok")
    say(reply)


def _warm_up():
    """Load the speech model and the LLM in the background, so the first
    real command isn't slow (and any one-time downloads happen now)."""
    try:
        transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))
        log("Speech recognition ready.", "ok")
    except Exception as e:
        log(f"Speech recognition not ready: {e}", "warn")
    from ..core import brain
    if brain.warm_up():
        log(f"Brain online ({brain.MODEL}).", "ok")
    else:
        log("Brain (Ollama) not responding yet.", "warn")


def _make_detector():
    """Create the wake-word detector, retrying (e.g. first-run download
    while offline) instead of crashing."""
    delay = 5
    while True:
        try:
            return WakeWordDetector(keyword="jarvis", threshold=WAKE_THRESHOLD,
                                    on_level=lambda lvl: report(mic=lvl))
        except Exception as e:
            log(f"Wake word not ready ({type(e).__name__}: {e}). Retrying in {delay}s.", "warn")
            report(state="error", voice_status="wake word unavailable")
            time.sleep(delay)
            delay = min(60, delay * 2)


def run_voice_loop(on_transcript=None):
    """Main loop. Blocks forever until Ctrl+C / SIGTERM.

    on_transcript: callback(text). Defaults to the brain (handle_command).
    SECURITY: The callback receives TEXT ONLY, never raw audio.
    """
    if on_transcript is None:
        from ..skills.registry import build_skills
        skills = build_skills()
        on_transcript = lambda text: handle_command(text, skills)  # noqa: E731

    report(state="starting", voice_status="starting", log="Voice loop starting...")
    threading.Thread(target=_heartbeat, daemon=True).start()
    threading.Thread(target=_warm_up, daemon=True).start()
    threading.Thread(target=_watch_input_volume, daemon=True).start()
    threading.Thread(target=_mic_monitor, daemon=True).start()

    detector = _make_detector() if WAKE_ENGINE == "openwakeword" else None
    log("Online. Just say \"Jarvis, …\" — e.g. \"Jarvis, open Finder\".", "ok")
    report(state="idle")

    errors = 0
    try:
        while True:
            try:
                report(state="idle", task="")
                if detector is not None:
                    # Legacy engine: "Hey Jarvis" model, then listen.
                    detector.wait_for_wake()
                    log("Wake word heard.")
                    command = ""
                else:
                    command = _standby_utterance()
                    if command is None:
                        continue
                # Wake word heard → the widget appears for this conversation.
                report(awake=True, transcript="", reply="", activity="", activity_status="", task="")

                if not command:
                    # Just "Jarvis" — chime, then listen (no spoken "Yes, sir").
                    command = _listen_once()
                    if not command:
                        log("Heard nothing after the wake word.")
                        report(awake=False)
                        continue
                    log(f"You: {command}")
                on_transcript(command)

                # Live conversation: keep listening for follow-ups (the chime
                # tells you when) until you go quiet or say "that's all".
                while FOLLOW_UP_SECONDS > 0:
                    text = _listen_once(start_timeout=FOLLOW_UP_SECONDS)
                    if not text:
                        log("Standing by — say \"Jarvis\" any time.")
                        break
                    woke, rest = split_wake(text)
                    if woke and rest:
                        text = rest  # "Jarvis, …" mid-conversation is fine too
                    log(f"You: {text}")
                    if _END_PHRASES.match(re.sub(r"[^\w\s']", "", text.lower()).strip()):
                        say("Very good, sir.")
                        break
                    on_transcript(text)
                # Conversation over → the widget fades away.
                report(awake=False, state="idle", task="")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                # e.g. mic unplugged mid-command. Recover, don't die.
                errors += 1
                if errors == 1 or errors % 30 == 0:   # don't flood the log
                    log(f"Voice loop error: {type(e).__name__}: {e}", "warn")
                report(state="error", awake=False)
                if isinstance(e, sd.PortAudioError):
                    # The mic changed (AirPods / iPhone / Zoom device came or
                    # went) or CoreAudio got stuck. PortAudio keeps its device
                    # list from startup, so reset it and use today's default mic.
                    _reset_audio()
                time.sleep(2)
            else:
                errors = 0
    except KeyboardInterrupt:
        print("\nJARVIS voice loop stopped.", flush=True)
    finally:
        if detector is not None:
            detector.close()
        report(state="idle", voice_status="stopped", task="")


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    _single_instance()
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    run_voice_loop()


if __name__ == "__main__":
    main()
