#!/usr/bin/env python3
"""
JARVIS mic permission onboarding.

Runs from the launcher BEFORE the voice loop starts. Opening the mic
triggers the macOS permission dialog ("JARVIS would like to access the
microphone"). Because the launcher script is the app bundle's own
executable, macOS attributes the request to JARVIS.app (using the
NSMicrophoneUsageDescription in Info.plist), and the choice is remembered.

v1.0.5 fix: when macOS DENIES the mic, opening the stream does NOT raise
an error — it just delivers pure digital silence (all zeros). v1.0.4
treated that as "granted". We now check for real signal.

Exit code 0 = mic works, 1 = no access (launcher shows instructions).
"""
import sys
import time


def _mic_has_signal(seconds: float = 0.6) -> bool:
    """Record briefly. A real, permitted mic always has some noise (non-zero
    samples). A denied mic gives exact zeros."""
    import numpy as np
    import sounddevice as sd
    rec = sd.rec(int(16000 * seconds), samplerate=16000, channels=1, dtype="float32")
    sd.wait()
    return bool(np.any(np.abs(rec) > 1e-7))


def check_mic_permission(wait_seconds: float = 0.0) -> bool:
    """True if we can actually hear audio. Optionally keep trying for a while
    (to give the user time to click 'Allow' on the macOS dialog)."""
    deadline = time.time() + wait_seconds
    while True:
        try:
            if _mic_has_signal():
                return True
        except Exception as e:  # no input device, PortAudio error, etc.
            print(f"Mic check error: {e}", file=sys.stderr)
        if time.time() >= deadline:
            return False
        time.sleep(1)


def main():
    print("=== JARVIS Microphone Setup ===", flush=True)
    print("Opening the microphone. If macOS asks, click 'OK' / 'Allow'.", flush=True)
    # First access triggers the dialog; give the user time to answer it.
    if check_mic_permission(wait_seconds=25):
        print("OK: Microphone access granted. JARVIS can hear you.", flush=True)
        return 0
    print("NO ACCESS: Microphone access was not granted.", flush=True)
    print("Enable it in System Settings > Privacy & Security > Microphone > JARVIS,", flush=True)
    print("then quit and reopen JARVIS.", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
