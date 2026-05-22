#!/usr/bin/env python3
"""
Re-apply all Scrypted performance patches after a plugin update or container recreate.

Usage:
    python3 /root/patches/apply-patches.py

Two patch types:
  - "copy":  copies a compiled JS file from the fork's dist/ into the container's
             node_modules. Use this for files owned in the fork TypeScript source.
  - "string": applies a targeted string substitution for files we don't own (plugin JS).
"""
import sys

PATCHES = [
    # ─── Server dist/ files (compiled from TypeScript fork) ─────────────────────
    # These replace the container's dist/ files with our compiled versions, which
    # include: MixinRebuildScheduler, RPC timeout middleware, and error classification.
    {
        "type": "copy",
        "name": "Server dist — plugin-device.js (error classification + setImmediate notify deferral)",
        "src": "/root/scrypted/fork/server/dist/plugin/plugin-device.js",
        "dst": "/server/node_modules/@scrypted/server/dist/plugin/plugin-device.js",
        "verify_src": "classifyRpcError",
    },
    {
        "type": "copy",
        "name": "Server dist — runtime.js (MixinRebuildScheduler wired in)",
        "src": "/root/scrypted/fork/server/dist/runtime.js",
        "dst": "/server/node_modules/@scrypted/server/dist/runtime.js",
        "verify_src": "mixinRebuildScheduler",
    },
    {
        "type": "copy",
        "name": "Server dist — mixin-rebuild-scheduler.js (new file)",
        "src": "/root/scrypted/fork/server/dist/plugin/mixin-rebuild-scheduler.js",
        "dst": "/server/node_modules/@scrypted/server/dist/plugin/mixin-rebuild-scheduler.js",
        "verify_src": "MixinRebuildScheduler",
    },
    {
        "type": "copy",
        "name": "Server dist — rpc.js (per-call RPC timeouts)",
        "src": "/root/scrypted/fork/server/dist/rpc.js",
        "dst": "/server/node_modules/@scrypted/server/dist/rpc.js",
        "verify_src": "RPC_TIMEOUT_MAP",
    },
    {
        "type": "copy",
        "name": "Server dist — rpc-timeout.js (new file)",
        "src": "/root/scrypted/fork/server/dist/rpc-timeout.js",
        "dst": "/server/node_modules/@scrypted/server/dist/rpc-timeout.js",
        "verify_src": "RpcTimeoutError",
    },
    {
        "type": "copy",
        "name": "Server dist — rpc-errors.js (new file)",
        "src": "/root/scrypted/fork/server/dist/rpc-errors.js",
        "dst": "/server/node_modules/@scrypted/server/dist/rpc-errors.js",
        "verify_src": "classifyRpcError",
    },

    # ─── Plugin files (string patches — files we don't own) ─────────────────────
    {
        # The Hikvision alertStream IIFE uses n.socket.setKeepAlive(true) but only calls
        # n.destroy() on error / close, not n.socket.destroy().  This leaves the TCP socket
        # open and the camera keeps streaming data into a dead buffer → recv-Q grows
        # continuously → Node.js event loop slows → plugin fails ping → cascade crash.
        "type": "string",
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
        "type": "string",
        "name": "ONVIF socket cleanup — destroy TCP socket on subscription teardown",
        "file": "/root/.scrypted/volume/plugins/@scrypted/onvif/zip/unzipped/main.nodejs.js",
        "old": 'destroy(){clearTimeout(o);try{t.unsubscribe()}catch(e){console.warn("Error unsubscribing",e)}}',
        "new": 'destroy(){clearTimeout(o);try{t.unsubscribe()}catch(e){console.warn("Error unsubscribing",e)}try{t.cam._socket?.destroy()}catch(e){}}',
        "verify": "t.cam._socket?.destroy()",
        "pyc": None,
    },
    {
        "type": "string",
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
        "type": "string",
        "name": "OpenVINO GPU THROUGHPUT mode",
        "file": "/root/.scrypted/volume/plugins/@scrypted/openvino/zip/unzipped/ov/__init__.py",
        "old": '"GPU_QUEUE_THROTTLE": "MEDIUM",\n                    "PERFORMANCE_HINT": "LATENCY",',
        "new": '"GPU_QUEUE_THROTTLE": "LOW",\n                    "PERFORMANCE_HINT": "THROUGHPUT",',
        "verify": '"GPU_QUEUE_THROTTLE": "LOW"',
        "pyc": "/root/.scrypted/volume/plugins/@scrypted/openvino/zip/unzipped/ov/__pycache__/__init__.cpython-312.pyc",
    },
]

import os
import shutil
import subprocess

all_ok = True

for patch in PATCHES:
    patch_type = patch.get("type", "string")
    print(f"\n[Patch] {patch['name']}")

    if patch_type == "copy":
        src = patch["src"]
        dst = patch["dst"]
        verify_src = patch.get("verify_src", "")

        if not os.path.exists(src):
            print(f"  ✗ Source not found: {src} — rebuild the fork first: cd /root/scrypted/fork/server && npm run build")
            all_ok = False
            continue

        # Check source has expected content
        with open(src, "r", errors="replace") as f:
            src_content = f.read()
        if verify_src and verify_src not in src_content:
            print(f"  ✗ Source missing expected marker '{verify_src}' — rebuild may be stale")
            all_ok = False
            continue

        # Check if dst already matches src
        if os.path.exists(dst):
            with open(dst, "r", errors="replace") as f:
                dst_content = f.read()
            if dst_content == src_content:
                print(f"  ✓ Already up to date")
                continue
            # Backup existing
            backup = dst + ".pre-patch-backup"
            shutil.copy2(dst, backup)
            print(f"  → Backup: {backup}")

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  ✓ Copied")
        continue

    # Default: "string" patch
    path = patch["file"]

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
    if patch.get("pyc") and os.path.exists(patch["pyc"]):
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
