"""
File skills — find, open, and organize files by voice.

SECURITY: This is the most dangerous skill family, so it's the most
locked down:
1. SANDBOX: Only ~/Documents, ~/Downloads, ~/Desktop are reachable.
   Any path outside these is refused — no exceptions, no symlinks out.
2. find_files is SAFE (read-only). move_file and delete_file are CONFIRM.
3. All paths resolved with .resolve() to defeat symlink escapes and
   ".." traversal before any check.
4. Deletes go to ~/.Trash via macOS, never rm — recoverable.
"""
from pathlib import Path
import shutil
import subprocess

# SECURITY: The sandbox. Changing this requires editing code AND the
# security.SKILL_LEVELS registry — deliberate, never accidental.
SANDBOX = [Path.home() / d for d in ("Documents", "Downloads", "Desktop")]


def _safe_path(p: str) -> Path:
    """Resolve a user-supplied path and verify it's inside the sandbox.

    SECURITY: .resolve() collapses symlinks and '..'. If the result
    isn't under a sandbox dir, raise — don't guess, don't warn, refuse.
    """
    raw = Path(str(p)).expanduser()
    if raw.is_absolute():
        target = raw.resolve()
    elif raw.parts and raw.parts[0] in {b.name for b in SANDBOX}:
        # "Documents/x.pdf" (the form find_files returns) is relative to home
        target = (Path.home() / raw).resolve()
    else:
        target = (SANDBOX[0] / raw).resolve()
    for base in SANDBOX:
        try:
            target.relative_to(base.resolve())
            return target
        except ValueError:
            continue
    raise PermissionError(f"Refusing to touch path outside sandbox: {p}")


def find_files(query: str = "", limit: int = 10) -> str:
    """Find files by name across the sandbox. Read-only, SAFE."""
    # SECURITY: Cap results. A one-letter query must not dump 10k paths.
    limit = max(1, min(20, int(limit)))
    q = "".join(c for c in str(query).lower() if c.isprintable())[:60]
    hits = []
    for base in SANDBOX:
        if not base.exists():
            continue
        # SECURITY: rglob stays under base; _safe_path not needed for
        # listing, but we never follow symlinks out (default rglob
        # doesn't follow dir symlinks).
        for f in base.rglob("*"):
            if len(hits) >= limit:
                break
            if q in f.name.lower() and not f.name.startswith("."):
                hits.append(str(f.relative_to(Path.home())))
    return "\n".join(hits) if hits else f"No files matching '{q}'."


def open_file(path: str = "") -> str:
    """Open a file with its default app, by path or just by its name. SAFE (opening is reversible)."""
    target = _safe_path(path)
    if not target.exists():
        # Spoken requests give a name, not a path — look for it.
        hit = find_files(Path(str(path)).name, limit=1)
        if hit.startswith("No files"):
            return f"I couldn't find {path}, sir."
        target = _safe_path(str(Path.home() / hit.splitlines()[0]))
    # SECURITY: `open` with a single argv entry — no shell.
    subprocess.run(["open", str(target)], check=False, timeout=10)
    return f"Opened {target.name}."


def move_file(src: str = "", dest_dir: str = "Documents") -> str:
    """Move a file within the sandbox. CONFIRM-level."""
    src_p = _safe_path(src)
    if not src_p.exists():
        return f"Couldn't find {src}, sir."
    # SECURITY: Destination must also be inside the sandbox.
    dest_base = _safe_path(dest_dir)
    dest_dir_p = dest_base if dest_base.is_dir() else dest_base.parent
    dest = dest_dir_p / src_p.name
    # SECURITY: Never overwrite. Rename with a counter instead.
    counter = 1
    while dest.exists():
        dest = dest_dir_p / f"{src_p.stem} ({counter}){src_p.suffix}"
        counter += 1
    shutil.move(str(src_p), str(dest))
    return f"Moved to {dest.relative_to(Path.home())}."


def delete_file(path: str = "") -> str:
    """Move a file to Trash (recoverable). CONFIRM-level.

    SECURITY: We never rm. Trash is recoverable; rm is not. A voice
    mis-hear must never be permanent.
    """
    target = _safe_path(path)
    if not target.exists():
        return f"Couldn't find {path}, sir."
    # SECURITY: osascript Finder delete = moves to Trash. Single argv,
    # path double-quote stripped to prevent AppleScript breakout.
    safe = str(target).replace('"', "")
    osa = shutil.which("osascript")
    subprocess.run(
        [osa, "-e", f'tell application "Finder" to delete POSIX file "{safe}"'],
        check=False, timeout=10,
    )
    return f"Moved {target.name} to Trash, sir."
