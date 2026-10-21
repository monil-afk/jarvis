"""
JARVIS native macOS app — real Cocoa window, not a browser tab.

Uses pywebview to create a native macOS window (WKWebView under the
hood) that loads the HUD from the local server. The result:
- JARVIS icon in the Dock
- Native window chrome (traffic lights, fullscreen, etc.)
- No browser address bar, no tabs — feels like a real app

SECURITY: Loads http://127.0.0.1:8765 only. No remote content ever.
"""
import os
import threading
import time
import urllib.request
from pathlib import Path

import webview

HUD_URL = "http://127.0.0.1:8765"
WIDGET_URL = "http://127.0.0.1:8765/widget"

_APP_ROOT = Path(__file__).resolve().parent.parent          # .../Contents/Resources/app
_BUNDLED_LAUNCHER = _APP_ROOT / "launcher.sh"               # newest launcher (ships with updates)
_LIVE_LAUNCHER = _APP_ROOT.parent.parent / "MacOS" / "JARVIS"  # what double-click runs


def sync_launcher():
    """Keep Contents/MacOS/JARVIS identical to the launcher.sh shipped with
    the app code.

    Why: the v1.0.4 updater only copied Contents/Resources/app, so launcher
    fixes could never reach an installed app. Once this code arrives (via
    that same updater), it installs the new launcher for the NEXT launch.
    Safe to do now: the old launcher has already handed over to Python.
    """
    try:
        if not (_BUNDLED_LAUNCHER.is_file() and _LIVE_LAUNCHER.is_file()):
            return  # not running from inside JARVIS.app (e.g. dev checkout)
        new = _BUNDLED_LAUNCHER.read_bytes()
        if new and new != _LIVE_LAUNCHER.read_bytes():
            tmp = _LIVE_LAUNCHER.with_name("JARVIS.new")
            tmp.write_bytes(new)
            os.chmod(tmp, 0o755)
            os.replace(tmp, _LIVE_LAUNCHER)  # atomic swap
            print("Launcher updated for next launch.", flush=True)
    except Exception as e:
        print(f"Launcher sync skipped: {e}", flush=True)


def wait_for_server(timeout: int = 20) -> bool:
    """Block until the HUD server answers (or timeout)."""
    for _ in range(timeout * 2):
        try:
            urllib.request.urlopen(HUD_URL, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# Siri-sized orb: 150x150 canvas + 40px of invisible space for the caption.
WIDGET_W, WIDGET_H = 150, 190


def _widget_enabled() -> bool:
    try:
        import yaml
        with open(_APP_ROOT / "config" / "jarvis.yaml") as f:
            cfg = yaml.safe_load(f) or {}
        return bool((cfg.get("hud") or {}).get("widget", True))
    except Exception:
        return True


_POS_FILE = Path.home() / ".jarvis" / "widget.json"
# After the conversation ends, wait this long before the orb disappears.
HIDE_AFTER_S = 1.5


def _widget_position():
    """Where you last dragged it; else top-right of the main screen.
    Clamped on-screen (e.g. after unplugging a monitor)."""
    try:
        scr = webview.screens[0]
        sw, sh = int(scr.width), int(scr.height)
    except Exception:
        return None, None
    x, y = sw - WIDGET_W - 24, 48
    try:
        import json
        saved = json.loads(_POS_FILE.read_text())
        x, y = int(saved["x"]), int(saved["y"])
    except Exception:
        pass
    return max(0, min(x, sw - WIDGET_W)), max(0, min(y, sh - WIDGET_H))


def _save_position(x, y):
    try:
        import json
        _POS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _POS_FILE.write_text(json.dumps({"x": int(x), "y": int(y)}))
    except Exception:
        pass


def _follow_wake_word(widget):
    """Show the orb only while JARVIS is awake: it appears when you say
    "Jarvis" and fades away once the conversation is over."""
    import json
    shown, quiet_since = False, None
    while True:
        try:
            with urllib.request.urlopen(HUD_URL + "/api/state", timeout=2) as r:
                awake = bool(json.loads(r.read()).get("awake"))
        except Exception:
            awake = False
        now = time.time()
        if awake:
            quiet_since = None
            if not shown:
                widget.show()
                shown = True
        elif shown:
            quiet_since = quiet_since or now
            if now - quiet_since >= HIDE_AFTER_S:
                widget.hide()
                shown = False
        time.sleep(0.2)


def _create_widget():
    x, y = _widget_position()
    # Frameless + transparent + always on top: just the floating orb — no
    # window box, no title bar. No background_color (it would paint a box).
    kwargs = dict(width=WIDGET_W, height=WIDGET_H, min_size=(WIDGET_W, WIDGET_H),
                  frameless=True, transparent=True, on_top=True,
                  easy_drag=False, resizable=False, hidden=True)  # dragging: .pywebview-drag-region in widget.html
    if x is not None:
        kwargs.update(x=x, y=y)
    try:
        return webview.create_window("JARVIS Widget", WIDGET_URL, **kwargs)
    except TypeError:
        pass
    try:
        kwargs.pop("transparent", None)
        return webview.create_window("JARVIS Widget", WIDGET_URL, **kwargs)
    except TypeError:
        # Older pywebview without some options — keep the essentials.
        return webview.create_window("JARVIS Widget", WIDGET_URL, width=WIDGET_W,
                                     height=WIDGET_H, on_top=True, frameless=True, hidden=True)


def _wire_widget(widget):
    """Remember where you drag the orb; show/hide it with the wake word."""
    try:
        widget.events.moved += lambda x, y: _save_position(x, y)
    except Exception:
        pass
    threading.Thread(target=_follow_wake_word, args=(widget,), daemon=True).start()


def open_main_window():
    """Open the full JARVIS HUD as a native app window, plus the floating
    always-on-top widget (drag it anywhere; it closes with the main window)."""
    if not wait_for_server():
        print("ERROR: JARVIS server did not start.")
        return
    main = webview.create_window(
        "JARVIS",
        HUD_URL,
        width=1280,
        height=800,
        min_size=(900, 600),
        # Native macOS window styling
        background_color="#02070c",
    )
    widget = None
    if _widget_enabled():
        widget = _create_widget()

        def _close_widget():
            try:
                widget.destroy()
            except Exception:
                pass
        try:
            main.events.closed += _close_widget
        except Exception:
            pass
    # gui='cocoa' is automatic on macOS
    webview.start(_wire_widget, widget, debug=False) if widget else webview.start(debug=False)


def open_widget():
    """Open only the floating orb widget (frameless, transparent, always on top)."""
    if not wait_for_server():
        print("ERROR: JARVIS server did not start.")
        return
    widget = _create_widget()
    webview.start(_wire_widget, widget, debug=False)


if __name__ == "__main__":
    import sys
    sync_launcher()
    mode = sys.argv[1] if len(sys.argv) > 1 else "main"
    if mode == "widget":
        open_widget()
    else:
        open_main_window()
