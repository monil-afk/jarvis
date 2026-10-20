"""
Security gates — every skill passes through here before it runs.

SECURITY MODEL:
1. Each skill declares a permission level: safe, confirm, or blocked.
2. 'safe' skills run immediately (read volume, open app).
3. 'confirm' skills ask the user aloud first (delete, send, quit-all).
4. 'blocked' skills never run until explicitly enabled in config.
5. Every decision is written to the audit log in memory.py.

Why this exists: voice is error-prone. A misheard word must never
delete files, send messages, or change system state irreversibly.
"""
from enum import Enum

from . import memory


class Level(Enum):
    SAFE = "safe"        # read-only or trivially reversible
    CONFIRM = "confirm"  # ask the user aloud before running
    BLOCKED = "blocked"  # disabled until user enables in config


# SECURITY: Default-deny. A skill not listed here CANNOT run, even if
# its code exists. New skills must be deliberately added with a level.
SKILL_LEVELS = {
    # read-only / harmless
    "get_volume": Level.SAFE,
    "get_brightness": Level.SAFE,
    "list_apps": Level.SAFE,
    "take_screenshot": Level.SAFE,
    "read_calendar": Level.SAFE,
    "read_reminders": Level.SAFE,
    "find_files": Level.SAFE,
    "next_event": Level.SAFE,
    "morning_briefing": Level.SAFE,
    "tell_time": Level.SAFE,
    # state-changing but reversible
    "open_file": Level.SAFE,
    "open_folder": Level.SAFE,
    "web_search": Level.SAFE,   # reads the web in the background, speaks the answer
    "get_weather": Level.SAFE,
    "open_website": Level.SAFE,
    "create_note": Level.SAFE,  # adds a note; never edits or deletes
    "draft_email": Level.SAFE,  # opens a draft only — never sends
    "media_control": Level.SAFE,
    "set_timer": Level.SAFE,
    "set_volume": Level.SAFE,
    "set_brightness": Level.SAFE,
    "open_app": Level.SAFE,
    "focus_app": Level.SAFE,
    # state-changing, ask first
    "quit_app": Level.CONFIRM,
    "quit_all_apps": Level.CONFIRM,
    "delete_file": Level.CONFIRM,
    "move_file": Level.CONFIRM,
    "send_message": Level.CONFIRM,
    "send_email": Level.CONFIRM,
    "create_event": Level.CONFIRM,
    "add_reminder": Level.CONFIRM,
    "run_shortcut": Level.CONFIRM,  # a shortcut can do anything — ask first
    "sleep_mac": Level.CONFIRM,
    "lock_mac": Level.CONFIRM,
}


def check(skill_name: str) -> Level:
    """Return the permission level for a skill.

    SECURITY: Unknown skills return BLOCKED. This is default-deny:
    forgetting to register a skill fails closed, not open.
    """
    return SKILL_LEVELS.get(skill_name, Level.BLOCKED)


def authorize(skill_name: str, ask_user) -> bool:
    """Gate a skill execution.

    ask_user: callable(prompt) -> bool. In production this speaks the
    prompt via TTS and listens for yes/no. In tests it's a stub.

    SECURITY: The decision + skill name + timestamp go to the audit
    log regardless of outcome. Silent denials would hide attacks.
    """
    level = check(skill_name)
    if level == Level.SAFE:
        return True
    if level == Level.BLOCKED:
        memory.log_command(f"auth:{skill_name}", "denied (not registered)", False)
        return False
    # CONFIRM — ask aloud
    # SECURITY: The prompt names the exact action. "Do it?" is not
    # enough; the user must hear WHAT they're approving.
    approved = ask_user(f"Shall I proceed with {skill_name.replace('_', ' ')}?")
    memory.log_command(
        f"auth:{skill_name}",
        "approved by user" if approved else "denied by user",
        approved,
    )
    return approved
