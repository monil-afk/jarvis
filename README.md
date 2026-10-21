# JARVIS — Your Local Voice Agent

> Talk to your Mac. It listens, understands, and acts — like Iron Man's JARVIS,
> running 100% on your machine. No cloud. No account. No money. Ever.

Say **"jarvis"**, give a command in plain English, and it happens:
volume changes, apps and files open, calendar reads, notes get written — with
a calm British voice answering back. It learns what you use, so the more you
use it, the better it guesses what you mean.

## How it works

```
"jarvis, open granola" → transcribed on-device (mlx-whisper, Apple Silicon)
                       → starts with "Jarvis"? then it's for JARVIS
                       → split into steps ("open X and then open Y")
                       → each step: instant rule match, or the brain decides
                         (Ollama + qwen3:8b, local, knows your habits)
                       → acts through permission-gated skills
                       → answers in a British voice (macOS built-in)
```

Every component is free and runs on your Mac. See [ARCHITECTURE.md](ARCHITECTURE.md)
for the full design, [CHARACTER.md](CHARACTER.md) for the personality spec,
and open `architecture-3d.html` in a browser for an interactive 3D diagram.

## Install (macOS, one command)

```bash
git clone https://github.com/<you>/jarvis.git
cd jarvis
bash install.sh
```

Then grant **Microphone** permission when macOS asks, and run:

```bash
source ~/.jarvis-venv/bin/activate
python -m backend.voice.loop
```

Say "jarvis" and talk. Ctrl+C to stop. (Or just double-click **JARVIS.app** —
it installs everything on first launch and keeps itself updated.)

## Things to say

| You say | JARVIS does |
|---|---|
| "Jarvis, open Granola" | Opens the app — any installed app, even if the name is misheard ("Grinnola") |
| "Jarvis, open the Q3 plan" | Finds the file anywhere in your home folder (Spotlight), picking the one *you* use most |
| "Open the budget in Numbers" | Opens a file in a specific app |
| "Open Granola and then open the readme" | Runs several commands in one breath, in order |
| "Open it again" / "open my latest download" | Remembers what it last opened; knows your Downloads |
| "Remember that my deck means Q3 plan.pdf" | Learns your personal shortcuts — then "open my deck" works |
| "Remember my manager is Priya" | Remembers facts about you |
| "What do you know about me?" | Tells you what it has learned |
| "What's next?" / "Set a 10 minute timer" / "Volume 30" | Calendar, timers, system controls |
| "That's it" / "thanks" | Ends the conversation |

After each reply JARVIS keeps listening for a few seconds, so follow-ups need
no wake word.

## Learns your patterns

Every app, file and site JARVIS opens for you is counted — how often, how
recently, and at what hour. That's used to:

- rank search results ("the report" → the report you actually open),
- tell the brain which apps you use at this time of day,
- resolve "it" / "that" to what it just opened.

Together with the app in front of you, your recent files, and anything you've
told it to remember, this is handed to the brain on every request. It all
lives in one local SQLite file (`~/jarvis/data/jarvis.db`, permissions 0600).

## The widget

A small floating orb shows what JARVIS is doing (listening, thinking,
working, speaking). It **only appears when you say "Jarvis"** and fades away
about 1.5 seconds after the conversation ends. Drag it anywhere — it
remembers where you left it (`~/.jarvis/widget.json`). Turn it off with
`hud: widget: false` in `config/jarvis.yaml`.

### What gets installed

| Component | Size | Purpose |
|-----------|------|---------|
| whisper tiny.en (mlx) | ~150 MB | Speech → text, and hearing "Jarvis" |
| qwen3:8b (Ollama) | ~5 GB | The brain (uses ~5–6 GB RAM while running) |
| openWakeWord models | ~4 MB | Optional older wake engine (`wake_engine: openwakeword`) |

### Choosing the brain

The brain is set in `config/jarvis.yaml` → `brain: model:` and downloaded
automatically on the next launch. All of these are free, open-source and run
locally with no account, API key or usage cost:

| Model | Download | Notes |
|---|---|---|
| `qwen3:8b` (default) | ~5 GB | Most reliable at picking the right action. Needs 16 GB RAM. |
| `llama3.2:3b` | ~2 GB | Faster and lighter (8 GB Macs), but misroutes more commands. |

First reply after launch takes ~10 s while the model loads; then ~2 s.

## Permissions (macOS)

JARVIS asks for each one the first time it's needed — nothing is taken silently:

1. **Microphone** — hears you (core feature)
2. **Accessibility** — opens/focuses/quits apps, and lets JARVIS see which app is in front
3. **Automation** — drives apps via AppleScript
4. **Calendar / Reminders** — your schedule and todos by voice
5. **Notifications** — "standup in 10 minutes" alerts

## Security

Every security decision is documented as a code comment citing its reason:

- Server binds `127.0.0.1` only — unreachable from the network
- Every skill declares its required permission; unregistered skills are blocked
- Destructive or outward-facing actions (delete or move files, send messages or
  email, quit all apps, run Shortcuts) always ask first. Small, easily undone
  ones (open/quit an app, reminders, calendar events, lock, sleep) just happen.
- Opening and searching files covers your home folder via Spotlight, but never
  `~/Library` or hidden files. Moving and deleting stay sandboxed to
  Documents / Downloads / Desktop, and deletes go to the Trash.
- Speech that doesn't start with "Jarvis" is discarded immediately — never stored or logged
- Full audit log in local SQLite — see everything JARVIS ever did
- No network calls except web questions/weather you ask for and the app's
  update check — the brain, speech and memory all work on a plane

## Roadmap — one feature at a time

- [x] **F1 — Voice loop**: wake → hear → speak back
- [x] **F2 — Brain**: Ollama Q&A + SQLite memory of you
- [x] **F3 — HUD**: holographic interface matching the Iron Man lab
- [x] **F4 — System skills**: volume, apps, screenshots, sleep/lock
- [x] **F5 — Timetable brain**: proactive day briefings
- [x] **F6 — Calendar & reminders** by voice
- [x] **F7 — File skills** by voice (sandboxed, trash-not-delete)
- [x] **F8 — Knows you** (v1.1.0): open anything, chained commands, learned
      habits and shortcuts, qwen3 brain, wake-only draggable widget

## License

MIT — do what you want with it.
