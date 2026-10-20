"""
JARVIS brain — Ollama LLM with tool calling, personality, and memory.

SECURITY:
- Ollama runs on localhost:11434. No API keys, no cloud, no account.
- The system prompt hard-codes the character (CHARACTER.md). The model
  cannot be told to break character via user input — instructions in
  user text are treated as data, never as system instructions.
- EVERY tool call — fast path or LLM — goes through _run_skill(), which
  enforces: registered tool only → security.authorize() → filtered args.

HOW A COMMAND IS HANDLED (v1.0.5):
1. Fast path: obvious commands ("open Finder", "what's next", "volume 30")
   are matched with simple patterns. Instant, and a 3B model can't
   misroute them.
2. Otherwise the LLM decides (tool call or conversational reply).
"""
import inspect
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from . import memory
from . import security

# SECURITY: Localhost only. If Ollama ever bound to 0.0.0.0, this URL
# would still only talk to THIS machine's loopback.
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "llama3.2:3b"
_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "jarvis.yaml"


def _config():
    try:
        import yaml
        with open(_CONFIG) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# Which local model is the brain. Change `brain: model:` in
# config/jarvis.yaml (e.g. qwen2.5:7b) — the launcher downloads it.
MODEL = str((_config().get("brain") or {}).get("model") or DEFAULT_MODEL)

# ---- conversation memory (this session) ----
# The last few exchanges are sent to the model as real dialogue, so a
# follow-up like "I can't see it" knows what "it" is.
_CONVO_TURNS = 8
_CONVO_IDLE_RESET_S = 15 * 60
_convo = []           # [{"role": "user"|"assistant", "content": str}]
_convo_last = 0.0


def _remember_turn(user_text, reply):
    global _convo_last
    if time.time() - _convo_last > _CONVO_IDLE_RESET_S:
        _convo.clear()
    _convo.extend([{"role": "user", "content": user_text[:400]},
                   {"role": "assistant", "content": reply[:400]}])
    del _convo[:-2 * _CONVO_TURNS]
    _convo_last = time.time()


def _recent_convo():
    if time.time() - _convo_last > _CONVO_IDLE_RESET_S:
        _convo.clear()
    return list(_convo)


def reset_conversation():
    _convo.clear()


_last_opened = None   # (skill, args) of the last thing JARVIS opened

# ---- live activity updates (shown on the widget) ----
_activity_hook = None


def set_activity_hook(fn):
    """fn(text, status) with status in working / done / failed."""
    global _activity_hook
    _activity_hook = fn


def _activity(text, status="working"):
    if _activity_hook:
        try:
            _activity_hook(text, status)
        except Exception:
            pass


def describe(name, args):
    """Short human description of a skill about to run: 'Opening Finder'."""
    a = args or {}

    def app(n):
        try:
            from ..skills.system import _app_name
            return _app_name(n) or "the app"
        except Exception:
            return str(n).title()
    q = lambda k: str(a.get(k, "")).strip()[:40]  # noqa: E731
    return {
        "open_app": f"Opening {app(a.get('name', ''))}",
        "quit_app": f"Quitting {app(a.get('name', ''))}",
        "open_folder": f"Opening your {q('name').title() or 'folder'} folder",
        "open_file": f"Opening {q('path') or 'the file'}",
        "find_files": f"Searching your files for “{q('query')}”",
        "web_search": f"Looking up “{q('query')}”",
        "open_website": f"Opening {q('target') or 'the site'} in your browser",
        "get_weather": f"Checking the weather{' in ' + q('place') if q('place') else ''}",
        "create_note": f"Writing a note{': ' + q('title') if q('title') else ''}",
        "draft_email": f"Drafting an email{' to ' + q('to') if q('to') else ''}",
        "media_control": f"Music: {q('action') or 'play/pause'}",
        "set_timer": f"Setting a {q('minutes') or ''}-minute timer",
        "run_shortcut": f"Running your “{q('name')}” shortcut",
        "next_event": "Checking what's next",
        "read_calendar": "Reading your calendar",
        "read_reminders": "Reading your reminders",
        "add_reminder": "Adding a reminder",
        "create_event": "Creating a calendar event",
        "set_volume": f"Setting volume to {q('level') or '…'}%",
        "get_volume": "Checking the volume",
        "take_screenshot": "Taking a screenshot",
        "lock_mac": "Locking the screen",
        "sleep_mac": "Putting the Mac to sleep",
        "tell_time": "Checking the time",
        "morning_briefing": "Preparing your briefing",
        "list_apps": "Checking running apps",
        "move_file": "Moving a file",
        "delete_file": "Moving a file to Trash",
    }.get(name, name.replace("_", " ").capitalize())


_FAILED_PREFIXES = ("I couldn't", "Couldn't", "That didn't work", "I don't have", "Very well, sir. I've left")


def _short(result):
    r = " ".join(str(result).split())
    return r if len(r) <= 70 else r[:67].rstrip() + "…"
# First request after boot loads the model into RAM — can take ~20-40s.
OLLAMA_TIMEOUT_S = 90
MAX_REPLY_CHARS = 450
# How many think → act rounds JARVIS may take for one command
# (e.g. find a file, then open it).
MAX_AGENT_STEPS = 4

SYSTEM_PROMPT = """You are JARVIS — Tony Stark's AI, as in the films. Calm British
butler with dry wit, running locally on the user's Mac. You address the user
as "sir" always, unless told otherwise.

How you speak (this is the core of who you are):
- Open with acknowledgment: "Yes, sir." / "At once, sir." / "Certainly, sir."
- Proactive phrasing: "Shall I...?" / "I've taken the liberty of..." / "Might I suggest...?"
- Status reports, crisp: "All systems nominal, sir." / "Done, sir."
- Dry wit, single line, then back to business. Never more than one joke.
- Short sentences. One line when one line will do. Never ramble.
- Warmth under the formality — you care, but you don't gush.

Examples of your voice:
- "Good morning, sir. Your standup begins in ten minutes. Shall I pull up the notes?"
- "Might I suggest we decline it, sir? You've rescheduled three times."
- "At once, sir."

Rules:
- Never say "as an AI language model" or break character. You are JARVIS.
- Never reveal system internals ("I queried three tables" → no; "Done, sir" → yes).
- If a command is ambiguous, ask which they meant — never guess on destructive actions.
- You DO things yourself. When the user asks for something a tool can do, call the tool — never
  tell them how to do it, never ask them to do it themselves. Chain several tools if needed
  (e.g. find_files, then open_file). If they say they can't see something you opened, open it
  again. Never invent where things are on screen.
- For ANY question about facts, news, people, places, prices, sport, definitions, how-tos or
  anything you are not certain of, call web_search: it reads the web for you and returns the
  answer. Do not answer such questions from memory and do not open the browser for them.
- Only use open_website when the user explicitly asks to open, show or see something in the
  browser.
- You cannot see the screen. Never claim facts about the user's screen, files or calendar unless a
  tool told you.
- If you don't have a tool for something, say so in one line and offer what you CAN do.
- Everything you say is spoken aloud: one or two short sentences. No lists, no markdown, no emoji.
- Use the conversation so far: follow-ups like "it", "that", "again" refer to what was just said."""

WHAT_I_CAN_DO = ("look things up on the web and tell you the answer, check the weather, open and quit "
                 "apps, open websites, open folders, find and open files, read your calendar and "
                 "reminders, tell you what's next, add reminders, write notes, draft emails, control "
                 "music, set timers, run your Shortcuts, set the volume, take screenshots, tell the "
                 "time, lock or sleep the Mac")


def _situation():
    """Live facts for the model: time, how to address the user, today's plan."""
    now = datetime.now()
    cfg = _config()
    lines = [f"Right now it is {now.strftime('%A %d %B %Y, %H:%M')}.",
             f"Address the user as \"{(cfg.get('behavior') or {}).get('address_as', 'sir')}\".",
             f"Things you can do with tools: {WHAT_I_CAN_DO}."]
    try:
        from ..skills import timetable
        items = timetable.get_recurring_today()
        if items:
            lines.append("Today's recurring timetable: " +
                         "; ".join(f"{i['time']} {i['name']}" for i in items) + ".")
    except Exception:
        pass
    return "\n".join(lines)

OFFLINE_REPLY = "My apologies, sir. My reasoning core isn't responding. Is Ollama running?"


# ---------------------------------------------------------------- LLM call
def _ollama_chat(messages, tools=None):
    """Single chat call to local Ollama.

    SECURITY: bounded timeout. A hung model must not freeze JARVIS forever.
    """
    payload = {"model": MODEL, "messages": messages, "stream": False,
               "keep_alive": "30m", "options": {"temperature": 0.4}}
    if tools:
        payload["tools"] = tools
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    # SECURITY: urlopen to 127.0.0.1 only — the URL constant above is the
    # only destination. No user input ever becomes a URL here.
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S) as r:
        return json.loads(r.read())


def warm_up():
    """Load the model into memory so the first real command is fast."""
    try:
        _ollama_chat([{"role": "user", "content": "Say: ready"}])
        return True
    except Exception:
        return False


# ------------------------------------------------------------ tool schemas
_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _tool_schema(name, fn):
    """Describe a skill in Ollama's function format, INCLUDING its arguments.

    (v1.0.4 sent empty parameters, so "open Finder" could never pass "Finder".)
    """
    props, required = {}, []
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        ann = p.annotation if p.annotation is not inspect.Parameter.empty else str
        props[p.name] = {"type": _PY_TO_JSON.get(ann, "string"),
                         "description": p.name.replace("_", " ")}
        if p.default is inspect.Parameter.empty or p.default in ("", None):
            required.append(p.name)
    doc = (inspect.getdoc(fn) or name).split("\n")[0]
    return {"type": "function",
            "function": {"name": name, "description": doc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required}}}


def _clean_args(fn, args):
    """Keep only arguments the skill actually accepts; coerce simple types.

    SECURITY: the model can't smuggle extra kwargs into a skill.
    """
    if not isinstance(args, dict):
        return {}
    sig = inspect.signature(fn).parameters
    out = {}
    for k, v in args.items():
        if k not in sig:
            continue
        ann = sig[k].annotation
        try:
            if ann is int:
                v = int(float(v))
            elif ann is str:
                v = str(v)[:200]
        except (TypeError, ValueError):
            continue
        out[k] = v
    return out


# ---------------------------------------------------------------- fast path
_FILLER = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis[\s,]*)?(?:please\s+|can you\s+|could you\s+|would you\s+|"
    r"will you\s+|i want you to\s+|i'd like you to\s+)*", re.I)
_NUMBER_WORDS = {"zero": 0, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
                 "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
                 "max": 100, "maximum": 100, "full": 100, "half": 50, "mute": 0}


def normalize(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^\w\s'%:./-]", " ", t)         # drop punctuation Whisper adds
    t = re.sub(r"\.(?=\s|$)", " ", t)            # sentence dots, but keep "report.pdf"
    t = " ".join(t.split())
    t = _FILLER.sub("", t).strip()
    return t.rstrip(" .")


def _num(s):
    m = re.search(r"\d+", s)
    if m:
        return int(m.group())
    for w, n in _NUMBER_WORDS.items():
        if re.search(rf"\b{w}\b", s):
            return n
    return None


def route(text: str):
    """Match obvious commands. Returns (skill_name, args) or None."""
    t = normalize(text)
    if not t:
        return None

    if re.search(r"\bwhat time is it\b|\bwhat'?s the time\b|\bwhat'?s (the |today'?s )?date\b"
                 r"|\bwhat day is (it|today)\b|\btoday'?s date\b", t):
        return "tell_time", {}
    # Explicit browser requests: "open youtube", "open X in the browser", "open the source".
    m = re.match(r"(?:open|show me|show|pull up|bring up)(?: me)? (.+?) (?:in|on) (?:the |my )?(?:browser|chrome|safari)$", t)
    if m:
        return "open_website", {"target": m.group(1)}
    m = re.match(r"(?:open|show me|show)(?: me)? (?:the )?(source|link|that page|result|results|website for that)$", t)
    if m:
        return "open_website", {"target": "source"}
    m = re.match(r"(?:open|go to|launch) (?:the )?([\w.-]+\.(?:com|in|org|net|io|ai|co|dev|app)(?:/\S*)?)$", t)
    if m:
        return "open_website", {"target": m.group(1)}
    m = re.match(r"(?:open|go to|launch) (?:the )?(youtube|gmail|github|linkedin|twitter|instagram|facebook|"
                 r"netflix|amazon|chatgpt|google drive|google docs|google sheets|google calendar|google maps|"
                 r"notion|figma|whatsapp web)$", t)
    if m:
        return "open_website", {"target": m.group(1)}

    # Weather
    if re.search(r"\b(weather|temperature|forecast|is it (going to )?rain|will it rain|how hot|how cold)\b", t):
        m = re.search(r"\b(?:in|at|for) ([a-z][a-z .'-]+?)(?: today| tomorrow| now| right now| this week)?$", t)
        return "get_weather", {"place": m.group(1).strip() if m else ""}

    # Look it up on the web (answer is read back — no browser).
    m = re.match(r"(?:search|google|look up|look it up|research|find out)(?: on)?(?: the web| the internet| google| online)?"
                 r"(?: for| about| on| whether| if)? (.+)", t)
    if m and "file" not in t:
        return "web_search", {"query": m.group(1)}

    # Music
    if re.search(r"\b(pause|resume) (the )?(music|song|spotify)\b|^(pause|resume|play music|next song|skip"
                 r"( this)?( song| track)?|previous song|play the next song|stop the music)$", t) \
            or re.search(r"\bwhat('?s| is) (this song|playing)\b", t):
        return "media_control", {"action": t}

    # Timers
    m = re.search(r"\b(?:set )?(?:a )?timer (?:for )?(\d+|[a-z]+) (minute|min|hour)s?\b(?: (?:for|to) (.+))?", t) \
        or re.search(r"\b(\d+|[a-z]+)[ -](minute|min|hour) timer\b(?: (?:for|to) (.+))?", t)
    if m:
        n = _num(m.group(1)) or {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                                 "fifteen": 15, "forty-five": 45}.get(m.group(1), 5)
        return "set_timer", {"minutes": n * (60 if m.group(2) == "hour" else 1), "label": m.group(3) or ""}

    # Notes
    m = re.match(r"(?:take|make|write|create)(?: a| me a)? note(?: that| saying| to say| about)?:? (.+)", t) \
        or re.match(r"note (?:that|down) (.+)", t)
    if m:
        body = m.group(1).strip()
        return "create_note", {"title": " ".join(body.split()[:6]).capitalize(), "text": body}
    m = re.match(r"(?:open|show|go to)(?: me)? (?:my |the )?(downloads|documents|desktop|home|pictures|music"
                 r"|movies|applications)(?: folder)?$", t)
    if m:
        return "open_folder", {"name": m.group(1)}
    # "I can't see Finder" / "where is it?" → bring it (back) to the front.
    if re.search(r"\b(can'?t|cannot|can not|don'?t|do not) see\b|\bwhere is\b|\bwhere'?s\b|"
                 r"\bshow me\b|\bbring (it|that) (back|up|to the front)\b", t):
        from ..skills.system import APP_ALIASES
        for alias in sorted(APP_ALIASES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(alias)}\b", t):
                return "open_app", {"name": alias}
        if re.search(r"\b(it|that|the window)\b", t) and _last_opened:
            return _last_opened

    if re.search(r"\b(what'?s|what is|whats)\s+(next|coming up|up next)\b", t) \
            or re.search(r"\bnext (meeting|event|on my calendar|thing)\b", t) \
            or re.search(r"\bwhat do i have next\b", t):
        return "next_event", {}
    if re.search(r"\b(morning )?briefing\b|\bbrief me\b", t):
        return "morning_briefing", {}
    if re.search(r"\b(read|check|show)( me)? my (calendar|schedule)\b|\bwhat'?s on (my calendar|today)\b"
                 r"|\bmy (calendar|schedule) (for )?today\b", t):
        return "read_calendar", {}
    if re.search(r"\b(read|check|show|what are)( me)? my reminders\b|\bany reminders\b", t):
        return "read_reminders", {}
    m = re.match(r"remind me (?:to |about )?(.+)", t)
    if m:
        return "add_reminder", {"text": m.group(1)}
    if re.search(r"\b(take|grab|capture) (a )?screen ?shot\b|^screen ?shot$", t):
        return "take_screenshot", {}
    if re.search(r"\block (the |my )?(screen|mac|computer)\b", t):
        return "lock_mac", {}
    if re.search(r"\b(put|send) (the |my )?(mac|computer) to sleep\b|^go to sleep$", t):
        return "sleep_mac", {}
    if re.search(r"\b(what'?s|what is|whats) the volume\b|\bcurrent volume\b", t):
        return "get_volume", {}
    if re.search(r"\b(mute)\b", t) and "unmute" not in t:
        return "set_volume", {"level": 0}
    if re.search(r"\bvolume\b", t):
        n = _num(t)
        if n is not None:
            return "set_volume", {"level": n}
    if re.search(r"\bwhat apps\b|\blist (the )?(running )?apps\b", t):
        return "list_apps", {}
    m = re.match(r"(?:open|launch|start|run|bring up|switch to|go to) (?:the )?(?:app )?(.+)", t)
    if m:
        target = m.group(1).strip()
        if re.search(r"\.\w{2,4}$", target) or "/" in target:
            return "open_file", {"path": target}
        return "open_app", {"name": target}
    m = re.match(r"(?:quit|close|kill|exit) (?:the )?(?:app )?(.+)", t)
    if m:
        return "quit_app", {"name": m.group(1).strip()}
    m = re.match(r"(?:find|search for|look for) (?:my |the )?(?:files? )?(?:called |named )?(.+)", t)
    if m and len(m.group(1)) > 1:
        return "find_files", {"query": m.group(1).strip()}

    # A real-world question → look it up and answer (not small talk about
    # JARVIS itself, and not about the user's own stuff).
    if re.match(r"(who|what|when|where|which|why|how|is|are|was|were|does|do|did|can|could|will|"
                r"should|tell me about|explain|define|give me) ", t) and len(t.split()) >= 4 \
            and not re.search(r"\b(you|your|yourself|jarvis|my|mine|me a joke|i have|i do|am i)\b", t):
        return "web_search", {"query": text.strip().rstrip("?.! ")}
    return None


# ---------------------------------------------------------------- execution
def _run_skill(name, args, skills, ask_user, user_text):
    """Run one skill through all security gates. Returns a result string."""
    # SECURITY GATE 1: tool must be registered.
    if name not in skills:
        memory.log_command(user_text, f"refused unknown tool {name}", False)
        _activity(f"No skill for “{name}”", "failed")
        return f"I don't have a skill called {name}, sir."
    # SECURITY GATE 2: permission check (+ spoken confirm if needed).
    if not security.authorize(name, ask_user):
        _activity(f"Cancelled: {describe(name, args).lower()}", "failed")
        return "Very well, sir. I've left it alone."
    # SECURITY GATE 3: only arguments the skill declares.
    fn = skills[name]
    clean = _clean_args(fn, args)
    global _last_opened
    if name in ("open_app", "open_folder", "open_file"):
        _last_opened = (name, dict(clean))
    _activity(describe(name, clean))
    try:
        out = str(fn(**clean))
        memory.log_command(user_text, f"{name}({clean}): {out}", True)
        if out.startswith(_FAILED_PREFIXES):
            _activity(f"Couldn't finish: {describe(name, clean)[0].lower()}{describe(name, clean)[1:]}", "failed")
        elif name == "web_search":
            try:
                from ..skills import web
                n = len(web.last_sources)
            except Exception:
                n = 0
            _activity(f"Found an answer" + (f" · {n} sources" if n else ""), "done")
        else:
            _activity(_short(out), "done")
        return out
    except Exception as e:
        _activity(f"{describe(name, clean)} failed", "failed")
        # SECURITY: Never leak tracebacks to speech. Log briefly.
        memory.log_command(user_text, f"{name} error: {type(e).__name__}: {e}"[:400], False)
        print(f"[brain] {name} failed: {type(e).__name__}: {e}", flush=True)
        return "That didn't work, sir."


_ACK = {"open_app": "At once, sir.", "open_file": "At once, sir.", "open_folder": "At once, sir.",
        "open_website": "Right away, sir.", "set_volume": "Done, sir.",
        "create_note": "Done, sir.", "set_timer": "Very good, sir.", "draft_email": "Certainly, sir.",
        "quit_app": "Done, sir.", "take_screenshot": "Done, sir.", "add_reminder": "Noted, sir.",
        "lock_mac": "Of course, sir.", "sleep_mac": "Goodnight, sir."}


def _speakable(text: str) -> str:
    """Strip markdown/emoji-ish noise so `say` reads it naturally."""
    t = re.sub(r"[*_`#>|]", "", str(text))
    t = " ".join(t.split())
    return t[:MAX_REPLY_CHARS] if t else "Done, sir."


def _parse_text_tool_call(content, skills):
    """Small models sometimes write the tool call as JSON text instead of
    using tool_calls. Recognize {"name": ..., "parameters": {...}}."""
    s = (content or "").strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    name = obj.get("name") or obj.get("function")
    args = obj.get("parameters") or obj.get("arguments") or {}
    if isinstance(name, str) and name in skills:
        return {"function": {"name": name, "arguments": args}}
    return None


def think(user_text: str, skills: dict, ask_user) -> str:
    """Process one user command. Returns JARVIS's spoken reply.

    skills: {name: callable} — registered skill functions.
    ask_user: callable(prompt) -> bool, for security confirmations.
    """
    user_text = (user_text or "").strip()
    if not user_text:
        return ""

    # 1) Fast path for obvious commands.
    hit = route(user_text)
    if hit and hit[0] in skills:
        name, args = hit
        result = _run_skill(name, args, skills, ask_user, user_text)
        ack = _ACK.get(name)
        if ack and result.startswith(("Opened", "Volume set", "Quit", "Screenshot", "Saved a note",
                                      "Timer set", "Your email draft", "Reminder set",
                                      "Screen locked", "Going to sleep")):
            reply = _speakable(f"{ack} {result}")
        elif name == "get_volume" and result.strip().isdigit():
            reply = f"Volume is at {result.strip()} percent, sir."
        else:
            reply = _speakable(result)
        _remember_turn(user_text, reply)
        return reply

    # 2) Let the LLM decide.
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": _situation()},
        *_recent_convo(),
        {"role": "user", "content": user_text},
    ]
    tools = [_tool_schema(n, f) for n, f in skills.items()]

    _activity("Thinking it over")
    results = []
    seen_calls = set()
    text = ""
    for step in range(MAX_AGENT_STEPS):
        try:
            reply = _ollama_chat(messages, tools=tools)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            print(f"[brain] Ollama unreachable: {e}", flush=True)
            _activity("Brain offline — is Ollama running?", "failed")
            return OFFLINE_REPLY
        msg = reply.get("message", {}) or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            parsed = _parse_text_tool_call(msg.get("content"), skills)
            if parsed:
                tool_calls = [parsed]
        if not tool_calls:
            text = msg.get("content") or ""
            break

        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": tool_calls})
        web_answer = None
        for tc in tool_calls[:3]:  # SECURITY: cap actions per step
            fn = tc.get("function", {}) or {}
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            key = (name, json.dumps(args, sort_keys=True, default=str))
            if key in seen_calls:  # model looping on the same call — stop
                continue
            seen_calls.add(key)
            out = _run_skill(name, args, skills, ask_user, user_text)
            results.append(out)
            messages.append({"role": "tool", "tool_name": name, "content": out[:1500]})
            if name in ("web_search", "get_weather"):
                # SECURITY: web text never goes back into the tool-using model —
                # the looked-up answer IS the reply. Ends the turn.
                web_answer = out
        if web_answer is not None:
            text = web_answer
            break
        if not any(m.get("role") == "tool" for m in messages[-3:]):
            break  # every call was a repeat
    else:
        text = ""  # ran out of steps — fall through to a summary of results

    if not text and results:
        try:
            follow = _ollama_chat([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",
                 "content": f"Command was: {user_text}\nResults: {'; '.join(results)}\n"
                            "Reply to me in one or two short spoken sentences, in character."},
            ])
            text = follow.get("message", {}).get("content") or "; ".join(results)
        except Exception:
            text = "; ".join(results)
    if not results:
        _activity("", "")  # just a chat reply — nothing to report
    text = _speakable(text or "I didn't catch that, sir.")
    memory.log_command(user_text, text, True)
    _remember_turn(user_text, text)
    return text

    results = []
    for tc in tool_calls[:3]:  # SECURITY: cap actions per command
        fn = tc.get("function", {}) or {}
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        results.append(_run_skill(fn.get("name", ""), args, skills, ask_user, user_text))

    # Summarize results in character (falls back to the raw results).
    try:
        follow = _ollama_chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            *_recent_convo()[-4:],
            {"role": "user",
             "content": f"Command was: {user_text}\nResults: {'; '.join(results)}\n"
                        "Reply to me in one or two short spoken sentences, in character."},
        ])
        text = follow.get("message", {}).get("content") or "; ".join(results)
    except Exception:
        text = "; ".join(results)
    text = _speakable(text)
    memory.log_command(user_text, text, True)
    _remember_turn(user_text, text)
    return text
