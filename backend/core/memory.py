"""
JARVIS memory — SQLite. Profile, preferences, command history, learned facts.

SECURITY: Single local file (~/jarvis/data/jarvis.db). No network, no sync.
Permissions 0600 on the file — only the user can read it. This is where
"knowing you so well" lives, so it gets the strictest file protection.
"""
import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path.home() / "jarvis" / "data" / "jarvis.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    command TEXT NOT NULL,
    result TEXT NOT NULL,
    success INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    kind TEXT NOT NULL,          -- app | file | folder | site
    name TEXT NOT NULL,          -- display name ("Granola", "Q3 plan.pdf")
    target TEXT NOT NULL,        -- what to open (app name / absolute path / url)
    count INTEGER NOT NULL DEFAULT 0,
    last_ts REAL NOT NULL,
    hours TEXT NOT NULL DEFAULT '',  -- 24 comma-separated counts: when you use it
    PRIMARY KEY (kind, target)
);
CREATE TABLE IF NOT EXISTS aliases (
    phrase TEXT PRIMARY KEY,     -- what you say ("my deck")
    meaning TEXT NOT NULL,       -- what you mean ("open Q3 plan.pdf")
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    original TEXT NOT NULL,
    corrected TEXT NOT NULL
);
"""


def _connect():
    # SECURITY: Create parent dirs with 0700 — no group/other access.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(DB_PATH.parent, 0o700)
    conn = sqlite3.connect(str(DB_PATH))
    # SECURITY: 0600 on the DB file itself. Memory holds personal data.
    if DB_PATH.exists():
        os.chmod(DB_PATH, 0o600)
    conn.executescript(_SCHEMA)
    return conn


def remember(key: str, value: str):
    """Store a learned fact, e.g. remember('standup_time', '11:00')."""
    with _connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO facts (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )


def recall(key: str):
    """Get a learned fact. Returns None if unknown."""
    with _connect() as c:
        row = c.execute("SELECT value FROM facts WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


def log_command(command: str, result: str, success: bool):
    """Audit trail — every command JARVIS runs, with its outcome.

    SECURITY: This is the accountability record. It is append-only;
    no API deletes from it.
    """
    with _connect() as c:
        c.execute(
            "INSERT INTO history (ts, command, result, success) VALUES (?, ?, ?, ?)",
            (time.time(), command[:500], result[:500], int(success)),
        )


def log_correction(original: str, corrected: str):
    """When the user corrects JARVIS, remember it so it learns."""
    with _connect() as c:
        c.execute(
            "INSERT INTO corrections (ts, original, corrected) VALUES (?, ?, ?)",
            (time.time(), original[:500], corrected[:500]),
        )


def recent_history(limit: int = 20):
    """Last N commands for context. Newest first."""
    with _connect() as c:
        rows = c.execute(
            "SELECT ts, command, result, success FROM history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"ts": r[0], "command": r[1], "result": r[2], "success": bool(r[3])}
            for r in rows
        ]


# ---------------------------------------------------------------- patterns
# What makes JARVIS "know you": every app / file / site you open is
# counted, with the hour of day. Ranking and suggestions come from here.
def record_use(kind: str, name: str, target: str):
    hour = time.localtime().tm_hour
    with _connect() as c:
        row = c.execute("SELECT count, hours FROM usage WHERE kind=? AND target=?",
                        (kind, target)).fetchone()
        hours = [int(x) for x in row[1].split(",")] if row and row[1] else [0] * 24
        hours[hour] += 1
        c.execute(
            "INSERT OR REPLACE INTO usage (kind, name, target, count, last_ts, hours) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, name[:200], target[:1000], (row[0] if row else 0) + 1, time.time(),
             ",".join(map(str, hours))))


def usage_score(kind: str, target: str) -> float:
    """How much you use this: frequency, recency, and this hour of day."""
    with _connect() as c:
        row = c.execute("SELECT count, last_ts, hours FROM usage WHERE kind=? AND target=?",
                        (kind, target)).fetchone()
    if not row:
        return 0.0
    count, last_ts, hours = row
    days = (time.time() - last_ts) / 86400
    now_h = [int(x) for x in hours.split(",")][time.localtime().tm_hour] if hours else 0
    return count + 5.0 / (1 + days) + 2 * now_h


def top_used(kind: str, limit: int = 8, this_hour: bool = False):
    """[(name, target)] you use most — overall, or around this time of day."""
    with _connect() as c:
        rows = c.execute("SELECT name, target, count, last_ts, hours FROM usage WHERE kind=?",
                         (kind,)).fetchall()
    h = time.localtime().tm_hour

    def score(r):
        hrs = [int(x) for x in r[4].split(",")] if r[4] else [0] * 24
        if this_hour:
            return hrs[h] + 0.5 * (hrs[h - 1] + hrs[(h + 1) % 24])
        return r[2] + 5.0 / (1 + (time.time() - r[3]) / 86400)
    rows = [r for r in rows if not this_hour or score(r) >= 2]
    return [(r[0], r[1]) for r in sorted(rows, key=score, reverse=True)[:limit]]


def recent_used(kind: str, limit: int = 5):
    with _connect() as c:
        return c.execute("SELECT name, target FROM usage WHERE kind=? ORDER BY last_ts DESC LIMIT ?",
                         (kind, limit)).fetchall()


def set_alias(phrase: str, meaning: str):
    with _connect() as c:
        c.execute("INSERT OR REPLACE INTO aliases (phrase, meaning, updated_at) VALUES (?, ?, ?)",
                  (phrase.lower().strip()[:120], meaning.strip()[:300], time.time()))


def get_alias(phrase: str):
    with _connect() as c:
        row = c.execute("SELECT meaning FROM aliases WHERE phrase=?",
                        (phrase.lower().strip(),)).fetchone()
    return row[0] if row else None


def all_aliases(limit: int = 30):
    with _connect() as c:
        return c.execute("SELECT phrase, meaning FROM aliases ORDER BY updated_at DESC LIMIT ?",
                         (limit,)).fetchall()


def all_facts(limit: int = 30):
    with _connect() as c:
        return c.execute("SELECT key, value FROM facts ORDER BY updated_at DESC LIMIT ?",
                         (limit,)).fetchall()
