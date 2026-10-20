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
│   │   ├── brain.py       # Ollama chat orchestration, tool-calling loop
│   │   ├── memory.py      # SQLite: profile, timetable, prefs, command history
│   │   ├── skills.py      # Skill registry — every action is a registered skill
│   │   └── security.py    # Permission gates + audit log (see Security below)
│   ├── voice/
│   │   ├── wake.py        # openWakeWord, listens for "jarvis" (~50MB model, local)
│   │   ├── stt.py         # mlx-whisper (Apple Silicon) or faster-whisper tiny.en
│   │   └── tts.py         # macOS `say` — built-in, offline, free
│   └── skills/
│       ├── system.py      # volume, brightness, sleep, lock, screenshot
│       ├── apps.py        # open / quit / focus apps
│       ├── files.py       # find, open, move files (sandboxed to allowed dirs)
│       ├── calendar_.py   # read/create calendar events
│       ├── reminders.py   # todos
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
| Wake word | openWakeWord ("jarvis") | Local, ~1% CPU, no cloud | Free |
| Speech→text | mlx-whisper (tiny/base) | Apple-Silicon optimized, ~1s latency | Free |
| Text→speech | macOS `say` | Built-in, offline voices | Free |
| Brain (LLM) | Ollama + llama3.2:3b (or qwen2.5:3b) | Local, ~2GB, good tool-calling | Free |
| Memory | SQLite | Zero-dependency, local file | Free |
| HUD | HTML/CSS/JS on localhost | No framework, instant load | Free |
| Server | FastAPI + WebSocket | Localhost-only binding | Free |

Fallback: if mlx-whisper misbehaves, faster-whisper `tiny.en` (CPU, ~75MB).

## How a command flows

```
"jarvis" → wake.py detects → HUD core pulses → mic records →
stt.py transcribes → brain.py (Ollama + skills + memory) decides →
skill executes (gated by security.py) → tts.py speaks result →
HUD panels update → everything logged to SQLite audit trail
```

Latency target: wake→response under 3 seconds on M-series.

## The "brain that knows you"

`config/jarvis.yaml` holds your profile: name, timezone, work hours,
standup times, recurring timetable. `timetable.py` reads it + your Mac
calendar and proactively surfaces what's next ("standup in 10 minutes").
Memory grows: every command and correction is stored, so it learns
phrasing you use. Nothing leaves the machine.

## Security (every rule gets a code comment citing its reason)

1. **Localhost-only server** — `server.py` binds `127.0.0.1`, never `0.0.0.0`.
   Reason: no other device on the network can reach JARVIS.
2. **Skill permission gates** — every skill declares `requires_permission`;
   `security.py` blocks unapproved skills and asks first.
   Reason: a voice mis-hear must never delete files or send messages.
3. **Destructive-action confirmation** — delete/send/quit-all always ask aloud.
   Reason: voice is error-prone; irreversible actions need a yes.
4. **Sandboxed file access** — file skills only touch allowed directories
   (~/Documents, ~/Downloads, ~/Desktop by default).
   Reason: limits blast radius of a misunderstood command.
5. **Audit log** — every action + timestamp + result in SQLite.
   Reason: you can always see what JARVIS did and why.
6. **No network calls by default** — brain, STT, TTS all offline.
   Reason: privacy; also works on a plane.
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

Each feature ships, you test it, then we move on. Nothing half-built piles up.
