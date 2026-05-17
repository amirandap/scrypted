#!/usr/bin/env python3
"""
Scrypted NVR plugin patcher
Applies bug fixes to the compiled NVR bundle via exact string replacement.
The NVR plugin (@scrypted/nvr) is closed-source; these patches target
main.nodejs.js directly.

Usage:
  python3 patch-nvr.py [--check] [--restart]

  --check    Verify patch status without modifying anything
  --restart  Terminate the NVR plugin process after patching (watchdog restarts it)
"""
import os
import sys
import signal
import subprocess

PLUGIN_PATH = os.path.expanduser(
    "~/.scrypted/volume/plugins/@scrypted/nvr/zip/unzipped/main.nodejs.js"
)

PATCHES = [
    # Fix 1: mkdir before writeFile in Wa() — session metadata writer (pruner side)
    # Prevents ENOENT crash when a new recording directory has no parent yet.
    {
        "name": "mkdir before writeFile in Wa() (session metadata)",
        "original": (
            "async function Wa(e,t){"
            "await oe.writeFile(e,JSON.stringify(t))"
        ),
        "patched": (
            "async function Wa(e,t){"
            "await oe.mkdir(require('path').dirname(e),{recursive:true});"
            "await oe.writeFile(e,JSON.stringify(t))"
        ),
    },

    # Fix 2: mkdir before writeFile in fetchRecordingStreamThumbnail
    # Prevents ENOENT crash when the thumbnail cache directory doesn't exist.
    {
        "name": "mkdir before writeFile in fetchRecordingStreamThumbnail",
        "original": (
            "await oe.writeFile(s,i)"
            "}catch(e){this.console.error"
        ),
        "patched": (
            "await oe.mkdir(require('path').dirname(s),{recursive:true});"
            "await oe.writeFile(s,i)"
            "}catch(e){this.console.error"
        ),
    },

    # Fix 3a: Raise pruner gc() default free-pct threshold 15% -> 20%
    # gc() is the core garbage-collection loop; this is its default trigger level.
    {
        "name": "gc() default pct threshold 0.15 -> 0.20",
        "original": "async gc(e,t,i=!1,n=.15){",
        "patched":  "async gc(e,t,i=!1,n=.20){",
    },

    # Fix 3b: Pruner passes 2x minFreeSpaceGb instead of 1x for extra headroom.
    # Gives the pruner a larger lead time before recordings are refused.
    {
        "name": "Pruner bu() passes computeMinFreeSpaceGb()*2",
        "original": "this.gc(e,this.computeMinFreeSpaceGb(),!0)",
        "patched":  "this.gc(e,this.computeMinFreeSpaceGb()*2,!0)",
    },

    # Fix 3c: Recording-stop threshold lowered to 10% / 0.5x so the pruner
    # (tier 1 at 20% / 2x) always fires well before recordings are refused.
    {
        "name": "Recording stop 0.15/1x -> 0.10/0.5x (last-resort tier)",
        "original": (
            "if(f<.15||r<this.computeMinFreeSpaceGb())"
            "{this.console.warn"
        ),
        "patched": (
            "if(f<.10||r<this.computeMinFreeSpaceGb()*.5)"
            "{this.console.warn"
        ),
    },
]


def load():
    with open(PLUGIN_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def save(content):
    with open(PLUGIN_PATH, "w", encoding="utf-8") as fh:
        fh.write(content)


def check_mode(content):
    all_ok = True
    for p in PATCHES:
        if p["patched"] in content:
            print(f'  [APPLIED]  {p["name"]}')
        elif p["original"] in content:
            print(f'  [MISSING]  {p["name"]}')
            all_ok = False
        else:
            print(f'  [UNKNOWN]  {p["name"]}  <- neither original nor patched found')
            all_ok = False
    return all_ok


def apply_patches(content):
    for p in PATCHES:
        if p["patched"] in content:
            print(f'  [SKIP]     {p["name"]} (already applied)')
            continue
        if p["original"] not in content:
            print(f'  [ERROR]    {p["name"]} - original string not found!')
            sys.exit(1)
        content = content.replace(p["original"], p["patched"], 1)
        print(f'  [PATCHED]  {p["name"]}')
    return content


def stop_nvr():
    print("\nStopping NVR plugin (watchdog will restart it) ...")
    result = subprocess.run(
        ["pgrep", "-f", "child @scrypted/nvr"],
        capture_output=True, text=True
    )
    pids = [p for p in result.stdout.strip().split() if p.isdigit()]
    if not pids:
        print("  NVR process not found (may already be stopped)")
        return
    for pid in pids:
        os.kill(int(pid), signal.SIGTERM)
        print(f"  Sent SIGTERM to PID {pid}")
    print("  Watchdog will restart NVR automatically in ~15s")


def main():
    check = "--check" in sys.argv
    do_restart = "--restart" in sys.argv

    if not os.path.exists(PLUGIN_PATH):
        print(f"ERROR: NVR plugin not found at:\n  {PLUGIN_PATH}")
        sys.exit(1)

    content = load()

    if check:
        print("Patch status:")
        ok = check_mode(content)
        sys.exit(0 if ok else 1)

    print("Applying patches:")
    patched = apply_patches(content)
    save(patched)
    print("\nAll patches applied successfully.")

    if do_restart:
        stop_nvr()


if __name__ == "__main__":
    main()
