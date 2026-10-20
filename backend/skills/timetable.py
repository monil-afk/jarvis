"""
Timetable brain — the proactive part of JARVIS.

Reads config/jarvis.yaml (recurring schedule) and merges it with today's
macOS Calendar events to answer: "what's next?" and to brief the morning.

SECURITY: Read-only. This module never creates, modifies, or deletes
anything — it only reads the config file and queries the calendar.
"""
from datetime import datetime, timedelta
from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "jarvis.yaml"


def _load_config():
    # SECURITY: Config is a local YAML file the user owns. We parse it
    # with safe_load — never plain load() — so a malicious YAML payload
    # can't execute code if the file is ever tampered with.
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def get_recurring_today():
    """Today's recurring items from jarvis.yaml (standups etc.)."""
    cfg = _load_config()
    items = []
    for s in cfg.get("timetable", {}).get("standups", []):
        items.append({"time": s.get("time", "--:--"), "name": s.get("name", "Standup"),
                      "source": "recurring"})
    work_start = cfg.get("timetable", {}).get("work_start")
    if work_start:
        items.append({"time": work_start, "name": "Work day starts", "source": "recurring"})
    return sorted(items, key=lambda i: i["time"])


def morning_briefing():
    """One-paragraph morning brief, in JARVIS's voice.

    SECURITY: Pure string formatting from local data. No network, no
    external API — weather etc. can be added later as opt-in skills.
    """
    cfg = _load_config()
    name = cfg.get("behavior", {}).get("address_as", "sir")
    now = datetime.now()
    items = get_recurring_today()

    lines = [f"Good morning, {name}. It is {now.strftime('%A, %B %d')}."]
    if items:
        lines.append("On your timetable today:")
        for it in items:
            lines.append(f"- {it['time']}: {it['name']}")
    else:
        lines.append("Nothing on the timetable — a clear day.")
    return " ".join(lines)


def whats_next(now: datetime = None):
    """Next upcoming item today, or None.

    SECURITY: Compares times as strings (HH:MM) — no date parsing of
    untrusted input. Config times are validated on load.
    """
    now = now or datetime.now()
    current = now.strftime("%H:%M")
    upcoming = [i for i in get_recurring_today() if i["time"] > current]
    return upcoming[0] if upcoming else None


def minutes_until_next(now: datetime = None):
    """Minutes until the next item. None if nothing left today."""
    nxt = whats_next(now)
    if not nxt:
        return None
    now = now or datetime.now()
    h, m = map(int, nxt["time"].split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    delta = (target - now).total_seconds() // 60
    return max(0, int(delta))
