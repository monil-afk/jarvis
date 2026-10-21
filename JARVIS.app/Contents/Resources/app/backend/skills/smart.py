"""
Smart open — "open <anything>" the way you mean it.

  "open granola"                 → the Granola app
  "open the q3 plan"             → Q3 Plan.pdf (Spotlight, ranked by how YOU use files)
  "open the budget in numbers"   → budget.xlsx in Numbers
  "open it again" / "open that"  → whatever JARVIS opened last
  "open my latest download"      → newest file in Downloads
  "open my deck"                 → whatever you taught it ("remember my deck means Q3 plan.pdf")

Every successful open is recorded in memory (frequency, recency, hour of
day) so the next guess is better — that's how JARVIS learns your patterns.

SECURITY: Opening is read-only and reversible, so search covers your home
folder via Spotlight — but never ~/Library, hidden files or app bundles.
Moving/deleting stays locked to the files.py sandbox. All subprocess
calls are argv lists; the Spotlight query is stripped of quote and escape
characters so it can't break out of the query string.
"""
import re
import subprocess
import time
from pathlib import Path

from ..core import memory
from . import system

HOME = Path.home()
_SKIP = ("/Library/", "/.", "/node_modules/", "/.Trash/", ".app/", "/site-packages/",
         "/Pictures/Photos Library", "/.jarvis-venv/")
# Spoken words → file extensions ("the budget spreadsheet" → .xlsx/.numbers).
_KIND_WORDS = {
    "pdf": (".pdf",), "spreadsheet": (".xlsx", ".xls", ".csv", ".numbers"),
    "excel": (".xlsx", ".xls"), "sheet": (".xlsx", ".csv", ".numbers"),
    "doc": (".docx", ".doc", ".pages", ".txt", ".md"), "document": (".docx", ".doc", ".pages", ".pdf"),
    "word": (".docx", ".doc"), "deck": (".key", ".pptx", ".pdf"), "presentation": (".key", ".pptx"),
    "slides": (".key", ".pptx"), "keynote": (".key",), "powerpoint": (".pptx",),
    "image": (".png", ".jpg", ".jpeg", ".heic"), "photo": (".png", ".jpg", ".jpeg", ".heic"),
    "screenshot": (".png",), "video": (".mp4", ".mov"), "text": (".txt", ".md"),
}
_NOISE = {"the", "my", "a", "an", "file", "files", "called", "named", "folder", "please", "up",
          "that", "this", "one", "from", "of", "on", "in", "thing", "things", "stuff", "whatever",
          "something", "some", "for", "me", "with", "about", "project", "and", "to"}

# The last thing opened — so "open it again" / "open that in Preview" work.
last = {"kind": None, "name": None, "target": None}


def _remember(kind, name, target):
    last.update(kind=kind, name=name, target=target)
    try:
        memory.record_use(kind, name, target)
    except Exception:
        pass


def _clean_query(q):
    q = "".join(c for c in str(q) if c.isprintable())
    return re.sub(r"[\"'\\*]", "", q).strip()[:80]


def _mdfind(words):
    q = " && ".join(f"kMDItemFSName == '*{w}*'cd" for w in words)
    try:
        return subprocess.run(["mdfind", "-onlyin", str(HOME), q], capture_output=True,
                              text=True, timeout=8).stdout.splitlines()
    except Exception:
        return []


def spotlight(query: str, limit: int = 40):
    """Files in your home folder whose name contains every word you said."""
    words = [w for w in re.split(r"[\s_\-.]+", _clean_query(query).lower()) if w and w not in _NOISE]
    exts = ()
    for w in list(words):
        if w in _KIND_WORDS:
            exts = _KIND_WORDS[w]
            words.remove(w)
    if not words:
        return [], exts
    out = _mdfind(words)
    if not out and len(words) > 1:
        # "jarvis readme": "jarvis" is the FOLDER. Let one word match the path.
        for w in words:
            rest = [x for x in words if x != w]
            out += [p for p in _mdfind(rest) if w in p.lower()]
    if not out and len(words) > 1:
        # "architecture diagram": no file has every word — take the best partial
        # matches (longest words first; _rank puts the closest name on top).
        for w in sorted(words, key=len, reverse=True)[:3]:
            if len(w) >= 4:
                out += _mdfind([w])[:30]
    hits = []
    for p in out:
        if any(s in p for s in _SKIP) or not p.startswith(str(HOME)):
            continue
        if exts and not p.lower().endswith(exts):
            continue
        hits.append(p)
        if len(hits) >= limit:
            break
    return hits, exts


def _rank(paths, query):
    """Best match first: name closeness + how often/recently YOU opened it + freshness."""
    q = " ".join(w for w in re.split(r"[\s_\-.]+", query.lower()) if w and w not in _NOISE)

    def score(p):
        path = Path(p)
        stem = re.sub(r"[_\-.]+", " ", path.stem.lower())
        s = 0.0
        low = p.lower()
        for w in q.split():
            s += 2.0 if w in stem else (0.7 if w in low else 0)
        if stem == q:
            s += 10
        elif stem.startswith(q):
            s += 5
        s += memory.usage_score("file", p) * 2
        try:
            age_days = (time.time() - path.stat().st_mtime) / 86400
            s += 3.0 / (1 + age_days)
        except OSError:
            return -1
        s -= 0.3 * len(path.relative_to(HOME).parts)   # buried deep = less likely
        if path.is_dir():
            s -= 1
        return s
    return sorted(paths, key=score, reverse=True)


def _open_path(path, app=""):
    args = ["open"] + (["-a", app] if app else []) + [str(path)]
    r = subprocess.run(args, capture_output=True, text=True, timeout=15)
    return r.returncode == 0


def find_any_file(query: str = "") -> str:
    """Search ALL your files (Spotlight) by name, best match first. Read-only."""
    hits, _ = spotlight(query)
    if not hits:
        return f"I couldn't find a file matching {query}, sir."
    ranked = _rank(hits, query)[:5]
    return "\n".join(str(Path(p).relative_to(HOME)) for p in ranked)


def _newest_download():
    d = HOME / "Downloads"
    files = [p for p in d.iterdir() if not p.name.startswith(".")] if d.exists() else []
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def open_anything(name: str = "", app: str = "") -> str:
    """Open whatever the user names: an app, a file (searched across all their files), a folder, a website, or "it/that" (the last thing opened). Optional app = open the file in that app."""
    raw = " ".join(str(name).split())[:120]
    key = raw.lower().strip(" .!?")
    key = re.sub(r"^(?:the|my|a|an)\s+", "", key)
    app_name = system._app_name(app) if app else ""

    # 0) Things you taught JARVIS: "my deck" → "Q3 plan.pdf"
    alias = memory.get_alias(key)
    if alias and alias.lower() != key:
        return open_anything(alias, app)

    # 1) "it" / "that" / "again" → the last thing opened
    if key in ("", "it", "that", "this", "it again", "that again", "again", "the same", "same file",
               "that file", "the file", "last file", "the last file") and last["target"]:
        if last["kind"] == "app" and not app_name:
            return system.open_app(last["target"])
        if _open_path(last["target"], app_name):
            _remember(last["kind"], last["name"], last["target"])
            return f"Opened {last['name']}" + (f" in {app_name}." if app_name else ".")

    # 2) newest download / most recent file
    if re.search(r"\b(latest|last|newest|recent|most recent)\b.*\bdownload", key):
        p = _newest_download()
        if p and _open_path(p, app_name):
            _remember("file", p.name, str(p))
            return f"Opened {p.name}" + (f" in {app_name}." if app_name else ".")
        return "Your Downloads folder is empty, sir."
    if re.search(r"^(?:any|a|some|my last|the last|last|recent|my recent|latest) ?(?:file|document)s?$", key):
        recent = memory.recent_used("file", 1)
        if recent and _open_path(recent[0][1], app_name):
            _remember("file", recent[0][0], recent[0][1])
            return f"Opened {recent[0][0]}, the last file you used."
        return "Which file, sir? Tell me a word from its name."

    # 3) an absolute or ~ path
    if key.startswith(("/", "~")):
        p = Path(raw).expanduser()
        if p.exists() and str(p.resolve()).startswith(str(HOME)) and _open_path(p, app_name):
            _remember("file", p.name, str(p))
            return f"Opened {p.name}."

    # 4) an installed app (unless the words say it's a file)
    says_file = bool(re.search(r"\.\w{2,4}$", key)) or any(
        w in _KIND_WORDS or w in ("file", "folder") for w in key.split())
    if not says_file and not app_name:
        if key in system.APP_ALIASES or system._match_installed(key):
            out = system.open_app(key)
            if out.startswith("Opened"):
                return out
        if key in system.FOLDERS:
            return system.open_folder(key)

    # 5) a file anywhere in your home folder, ranked by YOUR usage
    hits, _ = spotlight(key)
    if hits:
        best = _rank(hits, key)[0]
        if _open_path(best, app_name):
            _remember("file", Path(best).name, best)
            where = str(Path(best).parent.relative_to(HOME)) or "home"
            return f"Opened {Path(best).name} from {where}" + (f" in {app_name}." if app_name else ".")

    # 6) a website ("open youtube")
    try:
        from .web import SITES, open_website
        if key in SITES or re.search(r"\.(com|in|org|net|io|ai|co|dev|app)\b", key):
            return open_website(key)
    except Exception:
        pass
    # 7) last resort: maybe it IS an app with an odd name
    out = system.open_app(key)
    if out.startswith("Opened"):
        return out
    return f"I couldn't find an app or a file called {raw}, sir."


def remember_this(phrase: str = "", meaning: str = "") -> str:
    """Learn a personal shortcut: when the user says `phrase` they mean `meaning` (e.g. "my deck" means "Q3 plan.pdf")."""
    phrase = " ".join(str(phrase).split()).strip(" .")
    meaning = " ".join(str(meaning).split()).strip(" .")
    if not phrase or not meaning:
        return "What should I remember, sir?"
    phrase = re.sub(r"^(?:the|my)\s+", "", phrase.lower()) if not phrase.lower().startswith("my ") else phrase.lower()
    memory.set_alias(phrase, meaning)
    memory.set_alias(re.sub(r"^my\s+", "", phrase), meaning)
    return f"Noted, sir. {phrase.capitalize()} means {meaning}."


def remember_fact(fact: str = "") -> str:
    """Remember a fact about the user, e.g. "my manager is Priya" or "I prefer Chrome"."""
    fact = " ".join(str(fact).split()).strip(" .")[:200]
    if not fact:
        return "What should I remember, sir?"
    key = "fact:" + re.sub(r"\W+", "_", " ".join(fact.lower().split()[:4]))
    memory.remember(key, fact)
    return "I'll remember that, sir."


def what_you_know() -> str:
    """Tell the user what JARVIS has learned about them: facts, shortcuts, favourite apps and files."""
    parts = []
    facts = [v for k, v in memory.all_facts(10) if k.startswith("fact:")]
    if facts:
        parts.append("You told me: " + "; ".join(facts) + ".")
    al = {}
    for ph, m in memory.all_aliases(10):
        al.setdefault(m, ph)            # "deck"/"my deck" → say it once
    if al:
        parts.append("Shortcuts: " + "; ".join(f"{ph} means {m}" for m, ph in list(al.items())[:5]) + ".")
    apps = [n for n, _ in memory.top_used("app", 4)]
    if apps:
        parts.append("Your most used apps are " + ", ".join(apps) + ".")
    files = [n for n, _ in memory.top_used("file", 3)]
    if files:
        parts.append("Files you open most: " + ", ".join(files) + ".")
    return " ".join(parts) or "Not much yet, sir. I learn as you use me."


def frontmost() -> str:
    """The app in front and its window title (needs Accessibility; '' if not allowed)."""
    try:
        out = system._osascript(
            'tell application "System Events" to set p to first process whose frontmost is true\n'
            'set n to name of p\n'
            'try\nset w to name of front window of p\non error\nset w to ""\nend try\n'
            'return n & " — " & w')
        return out.strip(" —")
    except Exception:
        return ""
