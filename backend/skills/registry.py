"""
Skill registry — the single list of skills the brain may call.

SECURITY: A skill must be BOTH implemented here AND listed in
core/security.SKILL_LEVELS. Anything missing from SKILL_LEVELS is
default-denied by security.authorize(), so registering a function here
alone is never enough to let it run.
"""
from . import calendar_, files, smart, system, timetable, web


def morning_briefing() -> str:
    """Give the morning briefing: today's date and timetable."""
    return timetable.morning_briefing().replace("\n", " ")


def build_skills() -> dict:
    """{name: callable} for every skill the brain can use."""
    return {
        # system
        "get_volume": system.get_volume,
        "set_volume": system.set_volume,
        "list_apps": system.list_apps,
        "open_app": system.open_app,
        "quit_app": system.quit_app,
        "take_screenshot": system.take_screenshot,
        "open_folder": system.open_folder,
        "tell_time": system.tell_time,
        "create_note": system.create_note,
        "draft_email": system.draft_email,
        "media_control": system.media_control,
        "set_timer": system.set_timer,
        "run_shortcut": system.run_shortcut,
        # web (answers are read and spoken; browser only when asked)
        "web_search": web.web_search,
        "open_website": web.open_website,
        "get_weather": web.get_weather,
        "sleep_mac": system.sleep_mac,
        "lock_mac": system.lock_mac,
        # calendar / schedule
        "next_event": calendar_.next_event,
        "read_calendar": calendar_.read_calendar,
        "read_reminders": calendar_.read_reminders,
        "add_reminder": calendar_.add_reminder,
        "create_event": calendar_.create_event,
        "morning_briefing": morning_briefing,
        # "open anything" + learning about you
        "open_anything": smart.open_anything,
        "find_any_file": smart.find_any_file,
        "remember_this": smart.remember_this,
        "remember_fact": smart.remember_fact,
        "what_you_know": smart.what_you_know,
        # files (sandboxed to Documents / Downloads / Desktop)
        "find_files": files.find_files,
        "open_file": files.open_file,
        "move_file": files.move_file,
        "delete_file": files.delete_file,
    }
