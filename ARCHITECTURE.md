# JARVIS — Local Voice Agent for Mac

> 100% local. 100% free. Nothing paid, nothing in the cloud.
> A background agent, not an app. You talk, it acts.

## The photo → the UI

The attached Iron Man lab photo sets the visual language:
- Deep dark background, holographic cyan/blue panels with thin glowing borders
- A central circular "arc reactor" element = JARVIS core status (listening / thinking / speaking / idle)
- Floating panels around it: system stats, calendar/timetable, recent actions, waveform
- Monospace/technical typography, subtle scanlines, no clutter

The HUD is a single local web page served on `localhost`, styled to match.

## System layout

```
~/jarvis/
├── backend/
│   ├── server.py          # FastAPI + WebSocket, binds 127.0.0.1 ONLY (never 0.0.0.0)
│   ├── core/
│   │   ├── brain.py       # step splitter, fast-path router, Ollama tool-calling loop
│   │   ├── memory.py      # SQLite: facts, aliases, usage patterns, command history
│   │   └── security.py    # Permission gates + audit log (see Security below)
│   ├── voice/
│   │   ├── loop.py        # standby → "Jarvis, …" → command → follow-ups
│   │   ├── wake.py        # optional openWakeWord engine ("hey jarvis")
│   │   ├── stt.py         # mlx-whisper (Apple Silicon) or faster-whisper tiny.en
│   │   └── tts.py         # macOS `say` — built-in, offline, free
│   ├── native_app.py      # app window + floating widget (shown only while awake)
│   └── skills/
│       ├── registry.py    # the single list of skills the brain may call
│       ├── smart.py       # open_anything, Spotlight file search, remember, what-you-know
│       ├── system.py      # apps (fuzzy-matched to installed apps), volume, timers, notes, lock/sleep
│       ├── files.py       # move / delete files (sandboxed to allowed dirs)
│       ├── calendar_.py   # calendar events + reminders
│       ├── web.py         # web answers, weather, open websites
│       └── timetable.py   # the "brain that knows your day"
├── frontend/hud/
│   ├── index.html         # the holographic HUD
│   ├── hud.css            # Iron-Man-lab styling
│   └── hud.js             # WebSocket client, waveform, panels
├── config/jarvis.yaml     # your profile, timetable, preferences (plain text, yours)
├── data/jarvis.db         # SQLite memory (local only)
└── install.sh             # one-command setup: brew, pip, ollama pull, models
```

## Component choices (all free, all local)

| Part | Choice | Why | Cost |
|------|--------|-----|------|
| Wake word | Whisper transcript starts with "Jarvis" (default) or openWakeWord | One breath: "Jarvis, open X"; no separate model to mishear | Free |
| Speech→text | mlx-whisper tiny.en + vocabulary prompt | Apple-Silicon optimized; prompt includes "Jarvis" and your installed app names | Free |
| Text→speech | macOS `say` | Built-in, offline voices | Free |
| Brain (LLM) | Ollama + qwen3:8b (Apache-2.0; llama3.2:3b for 8 GB Macs) | Local, ~5GB, reliable tool-calling | Free |
| File search | Spotlight (`mdfind`) | Instant, already indexed by macOS | Free |
| Memory | SQLite | Zero-dependency, local file | Free |
| HUD | HTML/CSS/JS on localhost | No framework, instant load | Free |
| Server | FastAPI + WebSocket | Localhost-only binding | Free |

Fallback: if mlx-whisper misbehaves, faster-whisper `tiny.en` (CPU, ~75MB).

## How a command flows

```
mic → utterance → stt.py → starts with "Jarvis"?  (no → discarded, never logged)
    → widget appears → brain.split_steps(): "open X and then open Y" → [X, Y]
    → for each step:
        alias? ("my deck" → "Q3 plan.pdf")
        route(): regex fast path for obvious commands (instant)
        else Ollama with ~8 relevant tools + context (front app, last opened,
             habits at this hour, recent files, facts, shortcuts)
    → skill executes (gated by security.py) → usage recorded → tts.py speaks
    → listens for follow-ups (no wake word) → "that's it"/silence → widget hides
```

Latency: fast-path commands act in well under a second after transcription;
brain decisions ~2 s once the model is loaded (~10 s for the first one).

### open_anything — how "open X" is resolved

1. Your shortcuts (`remember that X means Y`)
2. "it" / "that" / "again" → the last thing opened
3. "latest download" / "any file" → newest download / last file you used
4. An installed app (exact, prefix or fuzzy match: "grinnola" → Granola)
5. A file anywhere in `~` via Spotlight (never `~/Library` or hidden files),
   ranked by name match + **how often and how recently you opened it** + freshness
6. A website ("youtube", "something.com")

`"open X in Y"` opens the file with app Y.

## The "brain that knows you"

`config/jarvis.yaml` holds your profile: name, timezone, work hours,
standup times, recurring timetable. `timetable.py` reads it + your Mac
calendar and proactively surfaces what's next ("standup in 10 minutes").

`memory.py` learns as you go (SQLite, `~/jarvis/data/jarvis.db`):

| Table | What | Used for |
|---|---|---|
| `usage` | every app/file/site opened: count, last time, 24-hour histogram | ranking file matches, "you usually open X now" |
| `aliases` | "my deck" → "Q3 plan.pdf" | personal shortcuts |
| `facts` | "my manager is Priya" | context for every answer |
| `history` | every command + result | audit trail |

Nothing leaves the machine.

## Security (every rule gets a code comment citing its reason)

1. **Localhost-only server** — `server.py` binds `127.0.0.1`, never `0.0.0.0`.
   Reason: no other device on the network can reach JARVIS.
2. **Skill permission gates** — every skill declares `requires_permission`;
   `security.py` blocks unapproved skills and asks first.
   Reason: a voice mis-hear must never delete files or send messages.
3. **Destructive-action confirmation** — delete/move files, send messages or
   email, quit-all and Shortcuts always ask aloud. Small reversible actions
   (open/quit an app, reminders, events, lock, sleep) run without asking.
   Reason: voice is error-prone; irreversible actions need a yes, but asking
   about everything made everyday use painful.
4. **Sandboxed file changes** — move/delete only touch ~/Documents,
   ~/Downloads, ~/Desktop. Opening/searching (read-only) covers ~ via
   Spotlight, excluding ~/Library and hidden files.
   Reason: limits blast radius of a misunderstood command.
5. **Audit log** — every action + timestamp + result in SQLite.
   Reason: you can always see what JARVIS did and why.
6. **No network calls by default** — brain, STT, TTS, memory all offline.
   Web answers/weather only when you ask; the launcher checks GitHub for updates.
   Reason: privacy; also works on a plane.
8. **Speech not addressed to JARVIS is discarded** — utterances that don't
   start with "Jarvis" are dropped immediately, never stored or logged.
7. **Secrets never in code** — config holds only non-secret prefs.
   Reason: nothing to leak if you share the code.

## Build order — one feature at a time

- **F1 — Voice loop**: wake word → record → STT → TTS echo. Proves audio works.
- **F2 — Brain**: Ollama Q&A wired to voice loop + SQLite memory of your profile.
- **F3 — HUD**: holographic frontend matching the photo, live status + waveform.
- **F4 — System skills**: volume, brightness, open apps, screenshots, sleep/lock.
- **F5 — Timetable brain**: proactive day briefing from config + calendar.
- **F6 — Calendar & reminders**: read/create events and todos by voice.
- **F7 — File skills**: find/open/organize files by voice.
- **F8 — Knows you (v1.1.0)**: open_anything, chained commands, usage patterns,
  shortcuts and facts, qwen3 brain, wake-only draggable widget.

Each feature ships, you test it, then we move on. Nothing half-built piles up.
