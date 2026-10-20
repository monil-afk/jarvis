"""
JARVIS backend server — FastAPI + HTTP polling for the HUD.

SECURITY:
- Binds 127.0.0.1 ONLY. Never 0.0.0.0. No other machine on the network
  can reach this server, even on the same Wi-Fi.
- No auth needed BECAUSE it's loopback-only. If binding ever fails over
  to all interfaces, that's a bug, not a feature.
- POST /api/state additionally refuses any caller that isn't loopback,
  accepts only a fixed set of keys, and caps every string's length.
- The HUD files are served from the local frontend/ directory only.

HOW STATE FLOWS (v1.0.5):
  voice loop process ──POST /api/state──▶ this server ◀──GET /api/state── HUD
"""
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

HOST = "127.0.0.1"  # SECURITY: loopback only. See module docstring.
PORT = 8765

app = FastAPI(title="JARVIS")
_frontend = Path(__file__).parent.parent / "frontend" / "hud"

_VALID_STATES = {"idle", "listening", "thinking", "working", "speaking", "starting", "error"}
_MAX_TEXT = 300
_MAX_LOG = 40
# If the voice loop hasn't checked in for this long, it's not running.
_VOICE_TIMEOUT_S = 15

_lock = threading.Lock()
_current_state = {
    "state": "starting",
    "transcript": "",
    "reply": "",
    "up_next": "",
    "mic": 0.0,
    "voice_status": "starting",
    "user_talking": False,
    "activity": "",
    "activity_status": "",
    "activity_ts": 0.0,
    "task": "",          # one-line "what JARVIS is doing" for the orb widget caption
}
_voice_last_seen = 0.0
_log = []        # [{"id": n, "ts": float, "msg": str, "level": str}]
_log_seq = 0

# Each state stays on the HUD for at least this long, so fast steps
# (e.g. a 0.3s "thinking") are still visible to a HUD polling twice a second.
_MIN_STATE_S = 0.9
# Queued (state, task) snapshots. The orb state and its caption travel
# together, so "working" is always shown with what it's working on.
_state_timeline = []  # [(show_from_ts, state, task)]


def _push_snapshot():
    """Queue the current (state, task) for display, keeping each snapshot on
    screen for at least _MIN_STATE_S."""
    st, task = _current_state["state"], _current_state.get("task", "")
    now = time.time()
    if _state_timeline:
        last_from, last_state, last_task = _state_timeline[-1]
        if (last_state, last_task) == (st, task):
            return
        show_from = max(now, last_from + _MIN_STATE_S)
    else:
        show_from = now
    _state_timeline.append((show_from, st, task))
    del _state_timeline[:-16]


def _push_state(s: str):  # kept for older callers
    _current_state["state"] = s
    _push_snapshot()


# Activity steps get the same treatment: "Opening Finder…" stays visible
# for at least this long even if the step finishes in 50ms.
_MIN_ACTIVITY_S = 0.8
_activity_timeline = []  # [(show_from_ts, text, status)]


def _push_activity(text: str, status: str):
    now = time.time()
    show_from = now
    if _activity_timeline:
        last_from, _, last_status = _activity_timeline[-1]
        if last_status == "working":
            show_from = max(now, last_from + _MIN_ACTIVITY_S)
    _activity_timeline.append((show_from, text, status))
    del _activity_timeline[:-12]


def _displayed_activity():
    now = time.time()
    shown = None
    for item in _activity_timeline:
        if item[0] <= now:
            shown = item
    while len(_activity_timeline) > 1 and _activity_timeline[1][0] <= now:
        _activity_timeline.pop(0)
    return shown


def _displayed():
    """(state, task) currently on screen."""
    now = time.time()
    shown = (_current_state["state"], _current_state.get("task", ""))
    for show_from, st, task in _state_timeline:
        if show_from <= now:
            shown = (st, task)
    # Drop entries that are fully in the past (keep the current one).
    while len(_state_timeline) > 1 and _state_timeline[1][0] <= now:
        _state_timeline.pop(0)
    return shown


def _displayed_state():
    return _displayed()[0]


@app.get("/")
async def hud():
    # SECURITY: Serves exactly one file. No directory listing, no
    # path traversal — FileResponse with a fixed path.
    return FileResponse(_frontend / "index.html")


@app.get("/widget")
async def widget():
    # SECURITY: Same as above — fixed path, widget mini-HUD.
    return FileResponse(_frontend / "widget.html")


# ---------- system stats (no extra dependencies) ----------
_stats_cache = {"ts": 0.0, "cpu": None, "mem": None}


def _cpu_percent():
    """Whole-machine CPU %, 0-100. Uses `ps` (macOS + Linux), no psutil."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "%cpu="], capture_output=True,
                             text=True, timeout=2).stdout
        total = sum(float(x) for x in out.split() if x.replace(".", "", 1).isdigit())
        return max(0.0, min(100.0, total / (os.cpu_count() or 1)))
    except Exception:
        return None


def _mem_percent():
    """Used memory %, 0-100. macOS: vm_stat + sysctl. Linux: /proc/meminfo."""
    try:
        if platform.system() == "Darwin":
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                       text=True, timeout=2).stdout.strip())
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2).stdout
            page = 16384
            pages = {}
            for line in vm.splitlines():
                if "page size of" in line:
                    page = int(line.split("page size of")[1].split()[0])
                elif ":" in line:
                    k, v = line.split(":", 1)
                    v = v.strip().rstrip(".")
                    if v.isdigit():
                        pages[k.strip()] = int(v)
            used = (pages.get("Pages active", 0) + pages.get("Pages wired down", 0)
                    + pages.get("Pages occupied by compressor", 0)) * page
            return max(0.0, min(100.0, 100.0 * used / total))
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0])
        return 100.0 * (1 - info["MemAvailable"] / info["MemTotal"])
    except Exception:
        return None


def _stats():
    # Cached for 3s so fast HUD polling never spawns lots of processes.
    now = time.time()
    if now - _stats_cache["ts"] > 3:
        _stats_cache.update(ts=now, cpu=_cpu_percent(), mem=_mem_percent())
    return _stats_cache["cpu"], _stats_cache["mem"]


# ---------- timetable (from config/jarvis.yaml) ----------
_tt_cache = {"ts": 0.0, "items": [], "up_next": ""}


def _timetable():
    now = time.time()
    if now - _tt_cache["ts"] > 30:
        try:
            from .skills import timetable
            items = timetable.get_recurring_today()
            nxt = timetable.whats_next()
            mins = timetable.minutes_until_next()
            up = f"{nxt['name']} at {nxt['time']}" if nxt else "Nothing else today."
            if nxt and mins is not None:
                up += f" (in {mins} min)"
            _tt_cache.update(ts=now, items=items, up_next=up)
        except Exception:
            _tt_cache.update(ts=now)
    return _tt_cache["items"], _tt_cache["up_next"]


def _brain_model():
    try:
        from .core.brain import MODEL
        return MODEL
    except Exception:
        return ""


def _add_log(msg: str, level: str = "info"):
    global _log_seq
    _log_seq += 1
    _log.append({"id": _log_seq, "ts": time.time(), "msg": msg[:_MAX_TEXT], "level": level})
    del _log[:-_MAX_LOG]


@app.get("/api/state")
async def api_state():
    cpu, mem = _stats()
    items, up_next = _timetable()
    with _lock:
        snap = dict(_current_state)
        snap["state"], task = _displayed()
        # Widget caption contract: task is empty while idle, so the caption
        # fades out and the orb stands alone.
        snap["task"] = "" if snap["state"] == "idle" else task
        act = _displayed_activity()
        if act:
            snap["activity_ts"], snap["activity"], snap["activity_status"] = act
        voice_online = (time.time() - _voice_last_seen) < _VOICE_TIMEOUT_S
        snap["voice_online"] = voice_online
        if not voice_online and snap["state"] not in ("starting", "error"):
            snap["state"] = "starting" if _voice_last_seen == 0 else "error"
        snap["cpu"] = cpu
        snap["mem"] = mem
        snap["timetable"] = items
        snap["brain_model"] = _brain_model()
        snap["up_next"] = snap.get("up_next") or up_next
        snap["log"] = list(_log)
    return snap


@app.post("/api/state")
async def api_state_update(request: Request):
    """The voice loop reports what it's doing here. Loopback callers only."""
    global _voice_last_seen
    # SECURITY: belt and braces — the server only binds 127.0.0.1, but
    # refuse anything that isn't loopback anyway.
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1"):
        return JSONResponse({"ok": False}, status_code=403)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False}, status_code=400)

    with _lock:
        _voice_last_seen = time.time()
        s = data.get("state")
        if isinstance(s, str) and s in _VALID_STATES:
            _current_state["state"] = s
        for key in ("transcript", "reply", "voice_status"):
            v = data.get(key)
            if isinstance(v, str):
                _current_state[key] = v[:_MAX_TEXT]
        tk = data.get("task")
        if isinstance(tk, str):
            _current_state["task"] = " ".join(tk.split())[:160]
        if "state" in data or "task" in data:
            _push_snapshot()
        ut = data.get("user_talking")
        if isinstance(ut, bool):
            _current_state["user_talking"] = ut
        act = data.get("activity")
        if isinstance(act, str):
            st = data.get("activity_status")
            _push_activity(act[:120], st if st in ("working", "done", "failed") else "")
        m = data.get("mic")
        if isinstance(m, (int, float)):
            _current_state["mic"] = max(0.0, min(100.0, float(m)))
        msg = data.get("log")
        if isinstance(msg, str) and msg:
            level = data.get("log_level") if data.get("log_level") in ("ok", "info", "warn") else "info"
            _add_log(msg, level)
    return {"ok": True}


async def broadcast(msg: dict):
    """Legacy in-process updater (kept for compatibility with old callers).

    SECURITY: Broadcasts are state snapshots (strings/numbers only).
    Never raw audio, never file contents.
    """
    with _lock:
        if msg.get("type") == "state" and msg.get("state") in _VALID_STATES:
            _current_state["state"] = msg["state"]
            if isinstance(msg.get("task"), str):
                _current_state["task"] = " ".join(msg["task"].split())[:160]
            _push_snapshot()
        elif msg.get("type") == "task":
            _current_state["task"] = " ".join(str(msg.get("text", "")).split())[:160]
            _push_snapshot()
        elif msg.get("type") == "transcript":
            _current_state["transcript"] = str(msg.get("text", ""))[:_MAX_TEXT]
        elif msg.get("type") == "up_next":
            _current_state["up_next"] = str(msg.get("text", ""))[:_MAX_TEXT]


app.mount("/static", StaticFiles(directory=_frontend), name="static")


def main():
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
