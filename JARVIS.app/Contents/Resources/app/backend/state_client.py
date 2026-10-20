"""
Tiny client the voice loop uses to tell the HUD server what JARVIS is doing.

Why this exists: the launcher runs the server and the voice loop as two
separate processes, so the voice loop can't call the server's functions
directly. It POSTs small JSON updates to http://127.0.0.1:8765/api/state
instead, and the HUD polls that same endpoint.

SECURITY:
- Talks to 127.0.0.1 only (constant below). No user input becomes a URL.
- Sends state strings and short text only — never audio.
- Fire-and-forget on a background thread with a short timeout: if the
  HUD server is down, voice keeps working.
"""
import json
import queue
import threading
import urllib.request

STATE_URL = "http://127.0.0.1:8765/api/state"


def _post(payload: dict):
    try:
        req = urllib.request.Request(
            STATE_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1).read()
    except Exception:
        pass  # HUD is optional; never let it break the voice loop


# One worker thread + queue, so updates arrive at the server IN ORDER
# (idle → listening → thinking → speaking never gets shuffled).
_q = queue.Queue(maxsize=200)


def _worker():
    while True:
        _post(_q.get())


threading.Thread(target=_worker, daemon=True, name="hud-state").start()


def report(**fields):
    """report(state="listening"), report(transcript="..."), report(mic=42) ...

    Allowed keys (server ignores anything else):
      state, transcript, reply, mic, log, log_level, voice_status,
      user_talking, activity, activity_status
    """
    try:
        _q.put_nowait(fields)
    except queue.Full:
        pass  # HUD is far behind (server down?) — drop, don't block voice


def report_sync(**fields):
    """Same as report(), but waits (used for heartbeats / important states)."""
    _post(fields)
