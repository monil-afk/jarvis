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
