"""
System skills — volume, brightness, apps, screenshots, sleep/lock.

SECURITY: Every function here is registered in security.SKILL_LEVELS
with its permission level. Destructive ones (quit_all, sleep, lock)
require user confirmation via security.authorize() before the brain
may call them. All macOS interaction goes through osascript —
no shell=True anywhere, arguments are passed as lists.
"""
import shutil
import subprocess
from pathlib import Path


def _osascript(*args: str) -> str:
    """Run an AppleScript snippet.

    SECURITY: args become argv entries, never a shell string.
    AppleScript itself can do damage, which is why callers of this
    helper are gated by security levels.
    """
    osa = shutil.which("osascript")
    if not osa:
        raise RuntimeError("osascript not found — not on macOS?")
    out = subprocess.run(
        [osa, "-e", *args],
        capture_output=True, text=True, timeout=10,
    )
    return out.stdout.strip()


# ---- volume ----
def get_volume() -> str:
    """Read the current output volume (0-100)."""
    return _osascript("output volume of (get volume settings)")


def set_volume(level: int = 50) -> str:
    """Set output volume. Clamped 0-100."""
    # SECURITY: Clamp input. A model hallucinating 999 must not break audio.
    level = max(0, min(100, int(level)))
    _osascript(f"set volume output volume {level}")
    return f"Volume set to {level}."


# ---- apps ----
def list_apps() -> str:
    """List currently running app names."""
    names = _osascript(
        'tell application "System Events" to get name of every process '
        'whose background only is false'
    )
    return names.replace(", ", "\n")


# Spoken names → real macOS app names. Whisper hears "chrome", the app is
# called "Google Chrome".
APP_ALIASES = {
    "chrome": "Google Chrome", "google chrome": "Google Chrome",
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code",
    "visual studio code": "Visual Studio Code", "code": "Visual Studio Code",
    "settings": "System Settings", "system settings": "System Settings",
    "system preferences": "System Settings", "preferences": "System Settings",
    "mail": "Mail", "email": "Mail", "messages": "Messages", "imessage": "Messages",
    "music": "Music", "spotify": "Spotify", "notes": "Notes", "calendar": "Calendar",
    "reminders": "Reminders", "terminal": "Terminal", "finder": "Finder",
    "safari": "Safari", "slack": "Slack", "zoom": "zoom.us", "whatsapp": "WhatsApp",
    "app store": "App Store", "photos": "Photos", "maps": "Maps", "facetime": "FaceTime",
    "activity monitor": "Activity Monitor", "calculator": "Calculator",
}


def _app_name(name) -> str:
    """Normalize a spoken app name. SECURITY: strip quotes, cap length."""
    raw = " ".join(str(name).replace('"', "").replace("'", "").strip(" .,!?").split())[:80]
    key = raw.lower()
    for suffix in (" app", " application"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
    if key in APP_ALIASES:
        return APP_ALIASES[key]
    return " ".join(w[:1].upper() + w[1:] for w in key.split()) or raw


def _bring_to_front(app: str):
    """Best effort — opening already succeeded, so never fail because of this."""
    try:
        _osascript(f'tell application "{app}" to activate')
    except Exception:
        pass


def open_app(name: str = "") -> str:
    """Open (or bring to front) an application by its name, e.g. Finder, Safari, Notes."""
    app = _app_name(name)
    if not app:
        return "Which application, sir?"
    if app == "Finder":
        # Finder is always "running" (it draws the desktop), so `open -a
        # Finder` only activates it and no window appears. Open a real
        # window on the home folder and bring it to the front instead.
        subprocess.run(["open", str(Path.home())], check=False, timeout=15)
        _bring_to_front("Finder")
        return "Opened Finder."
    # SECURITY: `open -a NAME` — argv list, no shell, no AppleScript
    # interpolation. Unlike `tell application "X" to activate`, it never
    # pops a "Where is X?" dialog when the app doesn't exist.
    r = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        # No such app — but "YouTube", "Gmail" etc. are websites. Open those.
        from .web import SITES, open_website
        if app.lower() in SITES:
            return open_website(app.lower())
        return f"I couldn't find an application called {app}, sir."
    # Bring it to the front even if it was already open in the background.
    _bring_to_front(app)
    return f"Opened {app}."


# ---- folders ----
FOLDERS = {"downloads": "Downloads", "documents": "Documents", "desktop": "Desktop",
           "home": "", "pictures": "Pictures", "music": "Music", "movies": "Movies",
           "applications": "/Applications"}


def open_folder(name: str = "") -> str:
    """Open a folder in a Finder window: Downloads, Documents, Desktop, Home, Pictures, Music, Movies or Applications."""
    key = str(name).lower().strip(" .").replace(" folder", "").removeprefix("my ").removeprefix("the ")
    if key not in FOLDERS:
        return "I can open Downloads, Documents, Desktop, Pictures, Music, Movies or your home folder, sir."
    sub = FOLDERS[key]
    path = sub if sub.startswith("/") else str(Path.home() / sub)
    # SECURITY: fixed allow-list of folders; argv list, no shell.
    subprocess.run(["open", path], check=False, timeout=15)
    _bring_to_front("Finder")
    return f"Opened your {key} folder."


# ---- time ----
def tell_time() -> str:
    """Tell the current time and date."""
    from datetime import datetime
    now = datetime.now()
    return f"It's {now.strftime('%-I:%M %p')}, sir, {now.strftime('%A, %B %-d')}."


# ---- do-it-for-me actions ----
def _as_text(t, limit=2000):
    """Text safe to embed in an AppleScript string literal."""
    t = "".join(c for c in str(t)[:limit] if c.isprintable() or c == "\n")
    return t.replace("\\", "\\\\").replace('"', '\\"')


def create_note(title: str = "", text: str = "") -> str:
    """Write a new note in Apple Notes with a title and text."""
    title = " ".join(str(title).split())[:120] or "Note from JARVIS"
    html_body = "<h1>" + _as_text(title, 120) + "</h1><p>" + _as_text(text).replace("\n", "<br>") + "</p>"
    _osascript('tell application "Notes"\nmake new note with properties {body:"' + html_body + '"}\nend tell')
    return f"Saved a note called {title}."


def draft_email(to: str = "", subject: str = "", body: str = "") -> str:
    """Open a new email draft in the Mail app with recipient, subject and body filled in (it is NOT sent)."""
    import urllib.parse
    to = "".join(c for c in str(to) if c.isprintable())[:200].replace(" ", "")
    q = urllib.parse.urlencode({"subject": str(subject)[:200], "body": str(body)[:3000]},
                               quote_via=urllib.parse.quote)
    # SECURITY: mailto only opens a draft — JARVIS never sends email itself.
    subprocess.run(["open", f"mailto:{urllib.parse.quote(to, safe='@,.+')}?{q}"], check=False, timeout=15)
    return "Your email draft is open for you to review and send."


def media_control(action: str = "playpause") -> str:
    """Control music in Spotify or Apple Music: play, pause, next, previous, or what's playing."""
    a = str(action).lower()
    verb = ("next track" if any(w in a for w in ("next", "skip")) else
            "previous track" if any(w in a for w in ("previous", "back", "last")) else
            "pause" if "pause" in a or "stop" in a else
            "play" if a.strip() == "play" or "resume" in a else
            "info" if any(w in a for w in ("what", "playing", "song", "info")) else "playpause")
    for app in ("Spotify", "Music"):
        running = _osascript(f'application "{app}" is running')
        if running != "true" and not (app == "Music" and verb in ("play", "playpause")):
            continue
        if verb == "info":
            out = _osascript(f'tell application "{app}" to if player state is playing then '
                             f'return (name of current track) & " by " & (artist of current track)')
            return f"Now playing {out}." if out else "Nothing is playing, sir."
        _osascript(f'tell application "{app}" to {verb}')
        return {"next track": "Skipped to the next track.", "previous track": "Back to the previous track.",
                "pause": "Paused the music.", "play": "Playing music."}.get(verb, "Toggled the music.")
    return "No music app is running, sir."


_timers = []


def set_timer(minutes: int = 5, label: str = "") -> str:
    """Set a countdown timer; JARVIS speaks and shows a notification when it's done."""
    import threading
    from ..voice.tts import speak
    mins = max(1, min(600, int(minutes)))
    what = " ".join(str(label).split())[:60]
    msg = f"Sir, your {mins}-minute timer{' for ' + what if what else ''} is done."

    def ring():
        try:
            _osascript(f'display notification "{_as_text(msg, 120)}" with title "JARVIS" sound name "Glass"')
        except Exception:
            pass
        speak(msg)
    t = threading.Timer(mins * 60, ring)
    t.daemon = True
    t.start()
    _timers.append(t)
    return f"Timer set for {mins} minute{'s' if mins != 1 else ''}{' for ' + what if what else ''}."


def run_shortcut(name: str = "") -> str:
    """Run one of the user's Apple Shortcuts by its name (asks for confirmation first)."""
    n = " ".join(str(name).split())[:100]
    if not n:
        return "Which shortcut, sir?"
    # SECURITY: argv list (no shell); CONFIRM-level in security.py because a
    # shortcut can do anything the user built it to do.
    r = subprocess.run(["shortcuts", "run", n], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return f"I couldn't run a shortcut called {n}, sir."
    out = " ".join(r.stdout.split())[:200]
    return f"Ran your {n} shortcut." + (f" It said: {out}" if out else "")


def quit_app(name: str = "") -> str:
    """Quit a running application by its name. Requires user confirmation (security gate)."""
    app = _app_name(name)
    if not app:
        return "Which application, sir?"
    # `if application "X" is running` avoids launching / "Where is X?" prompts.
    out = _osascript(f'if application "{app}" is running then\n'
                     f'tell application "{app}" to quit\nreturn "quit"\nend if\nreturn "not running"')
    if out == "not running":
        return f"{app} isn't running, sir."
    return f"Quit {app}."


# ---- screenshots ----
def take_screenshot() -> str:
    """Capture the main display to ~/Desktop/jarvis-screenshot.png."""
    # SECURITY: Fixed path under ~/Desktop. The skill cannot write
    # anywhere else — no path parameter accepted.
    from pathlib import Path
    dest = str(Path.home() / "Desktop" / "jarvis-screenshot.png")
    sc = shutil.which("screencapture")
    subprocess.run([sc, "-x", dest], check=False, timeout=10)
    return "Screenshot saved to Desktop."


# ---- power ----
def sleep_mac() -> str:
    """Put the Mac to sleep. Requires user confirmation (security gate)."""
    _osascript('tell application "System Events" to sleep')
    return "Going to sleep, sir."


def lock_mac() -> str:
    """Lock the screen. Requires user confirmation (security gate)."""
    _osascript(
        'tell application "System Events" to keystroke "q" '
        'using {control down, command down}'
    )
    return "Screen locked."
