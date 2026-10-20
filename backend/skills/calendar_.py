"""
Calendar & Reminders skills — read events, create events and todos by voice.

SECURITY:
- Reads go through macOS EventKit via osascript (Calendar/Reminders apps).
- Creating anything is a CONFIRM-level skill in security.py — JARVIS
  always asks aloud before adding to your calendar.
- Event titles are length-capped and control-char stripped, same as TTS.
"""
import shutil
import subprocess
from datetime import datetime


def _osascript(script: str) -> str:
    osa = shutil.which("osascript")
    if not osa:
        raise RuntimeError("osascript not found — not on macOS?")
    out = subprocess.run([osa, "-e", script],
                         capture_output=True, text=True, timeout=15)
    return out.stdout.strip()


def _clean(text: str, limit: int = 200) -> str:
    # SECURITY: Strip control chars + cap length. Calendar titles flow
    # into notifications and the HUD — no injection surface.
    t = "".join(c for c in str(text)[:limit] if c.isprintable() or c == " ")
    return " ".join(t.split())


def read_calendar() -> str:
    """Read today's calendar events."""
    script = '''
    tell application "Calendar"
      set todayEvents to {}
      repeat with cal in calendars
        repeat with ev in (every event of cal whose start date ≥ (current date) and start date < ((current date) + 1 * days))
          set end of todayEvents to (summary of ev & " at " & (time string of (start date of ev)))
        end repeat
      end repeat
      return todayEvents as string
    end tell
    '''
    out = _clean(_osascript(script), 1000)
    return out or "No more events today."


def _calendar_upcoming(hours: int = 24):
    """[(minutes_from_now, title), ...] for Calendar events in the next N hours."""
    hours = max(1, min(48, int(hours)))
    script = f'''
    set nowD to (current date)
    set endD to nowD + ({hours} * hours)
    set outText to ""
    tell application "Calendar"
      repeat with cal in calendars
        repeat with ev in (every event of cal whose start date ≥ nowD and start date < endD)
          set outText to outText & (((start date of ev) - nowD) as integer) & tab & (summary of ev) & linefeed
        end repeat
      end repeat
    end tell
    return outText
    '''
    items = []
    for line in _osascript(script).splitlines():
        if "\t" in line:
            secs, title = line.split("\t", 1)
            try:
                items.append((int(secs) // 60, _clean(title)))
            except ValueError:
                continue
    return sorted(items)


def _spoken_wait(mins: int) -> str:
    if mins < 1:
        return "now"
    if mins < 60:
        return f"in {mins} minute{'s' if mins != 1 else ''}"
    h, m = divmod(mins, 60)
    return f"in {h} hour{'s' if h != 1 else ''}" + (f" {m} minutes" if m else "")


def next_event() -> str:
    """Say what's next: the next upcoming calendar event or timetable item today."""
    candidates = []
    try:
        cal = _calendar_upcoming(24)
        if cal:
            candidates.append(cal[0])
    except Exception:
        pass  # Calendar not permitted / not available — fall back to timetable
    try:
        from . import timetable
        nxt = timetable.whats_next()
        mins = timetable.minutes_until_next()
        if nxt and mins is not None:
            candidates.append((mins, _clean(nxt["name"])))
    except Exception:
        pass
    if not candidates:
        return "Nothing else on the schedule today, sir."
    mins, title = min(candidates)
    when = (datetime.now().replace(second=0, microsecond=0).timestamp() + mins * 60)
    clock = datetime.fromtimestamp(when).strftime("%H:%M")
    return f"Next up: {title}, at {clock}, {_spoken_wait(mins)}."


def create_event(title: str = "New event", when: str = None) -> str:
    """Create a calendar event. CONFIRM-level: user is asked first.

    SECURITY: `when` accepts "HH:MM today" only. Free-form date parsing
    is a bug farm — we keep it strict and predictable.
    """
    title = _clean(title)
    # Default: one hour from now, 30-minute event.
    start = datetime.now().replace(second=0, microsecond=0)
    if when:
        try:
            h, m = map(int, when.split(":"))
            start = start.replace(hour=h, minute=m)
        except ValueError:
            return "I couldn't parse that time, sir. Try HH:MM."
    # Seconds from now — avoids locale-dependent AppleScript date strings
    # (and the old minute+30 overflow crash after half past the hour).
    offset = int((start - datetime.now().replace(second=0, microsecond=0)).total_seconds())
    title = title.replace('"', "")
    script = f'''
    set startD to (current date) + ({offset})
    set endD to startD + (30 * minutes)
    tell application "Calendar"
      tell calendar 1
        make new event with properties {{summary:"{title}", start date:startD, end date:endD}}
      end tell
    end tell
    '''
    _osascript(script)
    return f"Created '{title}' at {start.strftime('%H:%M')}, sir."


def read_reminders() -> str:
    """Read incomplete reminders."""
    script = '''
    tell application "Reminders"
      set todoNames to {}
      repeat with lst in lists
        repeat with rm in (every reminder of lst whose completed is false)
          set end of todoNames to name of rm
        end repeat
      end repeat
      return todoNames as string
    end tell
    '''
    out = _clean(_osascript(script), 1000)
    return out or "No open reminders."


def add_reminder(text: str = "") -> str:
    """Add a reminder with the given text. CONFIRM-level: user is asked first."""
    text = _clean(text).replace('"', "")  # SECURITY: no AppleScript breakout
    if not text:
        return "What shall I remind you about, sir?"
    script = f'''
    tell application "Reminders"
      tell list 1
        make new reminder with properties {{name:"{text}"}}
      end tell
    end tell
    '''
    _osascript(script)
    return f"Reminder set: {text}."
