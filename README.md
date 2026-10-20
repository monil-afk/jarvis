# JARVIS — Your Local Voice Agent

> Talk to your Mac. It listens, understands, and acts — like Iron Man's JARVIS,
> running 100% on your machine. No cloud. No account. No money. Ever.

Say **"jarvis"**, give a command in plain English, and it happens:
volume changes, apps open, calendar reads, files organize — with a calm
British voice answering back.

## How it works

```
"jarvis" → wake word (openWakeWord, on-device)
         → records your command
         → transcribes (mlx-whisper, Apple Silicon)
         → thinks (Ollama + llama3.2:3b, local)
         → acts through permission-gated skills
         → answers in the Magnus voice (macOS built-in)
```

Every component is free and offline. See [ARCHITECTURE.md](ARCHITECTURE.md)
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

Say "jarvis" and talk. Ctrl+C to stop.

### What gets installed

| Component | Size | Purpose |
|-----------|------|---------|
| openWakeWord model | ~50 MB | Hears "jarvis" |
| whisper tiny.en | ~150 MB | Speech → text |
| llama3.2:3b (Ollama) | ~2 GB | The brain |

## Permissions (macOS)

JARVIS asks for each one the first time it's needed — nothing is taken silently:

1. **Microphone** — hears you (core feature)
2. **Accessibility** — opens/focuses/quits apps
3. **Automation** — drives apps via AppleScript
4. **Calendar / Reminders** — your schedule and todos by voice
5. **Notifications** — "standup in 10 minutes" alerts

## Security

Every security decision is documented as a code comment citing its reason:

- Server binds `127.0.0.1` only — unreachable from the network
- Every skill declares its required permission; unapproved skills are blocked
- Destructive actions (delete, send, quit-all) always ask first
- File skills sandboxed to Documents / Downloads / Desktop
- Full audit log in local SQLite — see everything JARVIS ever did
- No network calls at runtime — works on a plane

## Roadmap — one feature at a time

- [x] **F1 — Voice loop**: wake → hear → speak back
- [x] **F2 — Brain**: Ollama Q&A + SQLite memory of you
- [x] **F3 — HUD**: holographic interface matching the Iron Man lab
- [x] **F4 — System skills**: volume, apps, screenshots, sleep/lock
- [x] **F5 — Timetable brain**: proactive day briefings
- [x] **F6 — Calendar & reminders** by voice
- [x] **F7 — File skills** by voice (sandboxed, trash-not-delete)

## License

MIT — do what you want with it.
