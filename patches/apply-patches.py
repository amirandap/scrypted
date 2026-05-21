#!/usr/bin/env python3
"""
Re-apply all Scrypted performance patches after a plugin update.
Run this after any Scrypted plugin update that touches openvino or objectdetector.

Usage:
    python3 /root/patches/apply-patches.sh
    # or:
    /root/patches/apply-patches.sh
"""
import sys

PATCHES = [
    {
        # The Hikvision alertStream IIFE uses n.socket.setKeepAlive(true) but only calls
        # n.destroy() on error / close, not n.socket.destroy().  This leaves the TCP socket
        # open and the camera keeps streaming data into a dead buffer → recv-Q grows
        # continuously → Node.js event loop slows → plugin fails ping → cascade crash.
        "name": "Hikvision alertStream socket cleanup — destroy TCP socket on stream close/error",
        "file": "/root/.scrypted/volume/plugins/@scrypted/hikvision/zip/unzipped/main.nodejs.js",
        "old": '.catch((()=>n.destroy())),e})),this.listenerPromis',
        "new": '.catch((()=>{try{n.socket?.destroy()}catch(e){}n.destroy()})),e})),this.listenerPromis',
        "verify": "n.socket?.destroy()",
        "pyc": None,
    },
    {
        # Without t.cam._socket?.destroy(), reconnected ONVIF subscriptions leave the
        # old HTTP socket in CLOSE-WAIT indefinitely.  Cameras with a 2-connection limit
        # (HI-IP3B OEM firmware) fill up and start dropping connections, which causes the
        # ONVIF plugin event loop to stall and fail its ping → cascade crash of all plugins.
        "name": "ONVIF socket cleanup — destroy TCP socket on subscription teardown",
        "file": "/root/.scrypted/volume/plugins/@scrypted/onvif/zip/unzipped/main.nodejs.js",
        "old": 'destroy(){clearTimeout(o);try{t.unsubscribe()}catch(e){console.warn("Error unsubscribing",e)}}',
        "new": 'destroy(){clearTimeout(o);try{t.unsubscribe()}catch(e){console.warn("Error unsubscribing",e)}try{t.cam._socket?.destroy()}catch(e){}}',
        "verify": "t.cam._socket?.destroy()",
        "pyc": None,
    },
    {
        "name": "HomeKit snapshot timeout — prevent hung camera from crashing plugin",
        "file": "/root/.scrypted/volume/plugins/@scrypted/homekit/zip/unzipped/main.nodejs.js",
        "old": "void r(null,await s(e));r(null,await s(e))",
        "new": (
            "void r(null,await Promise.race([s(e),new Promise((_,j)=>{const u=setTimeout("
            "()=>j(new Error(t.name+\" snapshot timed out after 6000ms\")),6000);u.unref()})]));"
            "r(null,await Promise.race([s(e),new Promise((_,j)=>{const u=setTimeout("
            "()=>j(new Error(t.name+\" snapshot timed out after 6000ms\")),6000);u.unref()})]))"
        ),
        "verify": "snapshot timed out after 6000ms",
        "pyc": None,
    },
    {
        "name": "OpenVINO GPU THROUGHPUT mode",
        "file": "/root/.scrypted/volume/plugins/@scrypted/openvino/zip/unzipped/ov/__init__.py",
        "old": '"GPU_QUEUE_THROTTLE": "MEDIUM",\n                    "PERFORMANCE_HINT": "LATENCY",',
        "new": '"GPU_QUEUE_THROTTLE": "LOW",\n                    "PERFORMANCE_HINT": "THROUGHPUT",',
        "verify": '"GPU_QUEUE_THROTTLE": "LOW"',
        "pyc": "/root/.scrypted/volume/plugins/@scrypted/openvino/zip/unzipped/ov/__pycache__/__init__.cpython-312.pyc",
    },
    # NOTE: Removed fps:8 cap patch — was a no-op for NVR object detection
    # because model.decoder=true causes generateVideoFrames() to be bypassed entirely.
    # See README.md for details.
]

import os
import subprocess

all_ok = True

for patch in PATCHES:
    path = patch["file"]
    print(f"\n[Patch] {patch['name']}")

    if not os.path.exists(path):
        print(f"  ✗ File not found: {path}")
        all_ok = False
        continue

    with open(path, "r", errors="replace") as f:
        content = f.read()

    if patch["verify"] in content:
        print(f"  ✓ Already applied")
        continue

    if patch["old"] not in content:
        print(f"  ✗ Old string not found — plugin may have been restructured, manual review needed")
        print(f"    Looking for: {patch['old'][:80]}")
        all_ok = False
        continue

    # Backup
    backup = path + ".pre-patch-backup"
    with open(backup, "w") as f:
        f.write(content)
    print(f"  → Backup: {backup}")

    content = content.replace(patch["old"], patch["new"], 1)

    with open(path, "w") as f:
        f.write(content)

    # Clear pyc cache if applicable
    if patch["pyc"] and os.path.exists(patch["pyc"]):
        os.remove(patch["pyc"])
        print(f"  → Cleared pyc cache")

    print(f"  ✓ Patch applied")

print()
if all_ok:
    print("All patches applied. Restart Scrypted to activate:")
    print("  docker restart scrypted")
else:
    print("Some patches need manual review (see above). Check README.md for details.")
    sys.exit(1)
