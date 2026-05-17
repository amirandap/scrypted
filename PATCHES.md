# amirandap/scrypted — Active Patches

All fixes live on the single branch: **`fixes/all-my-patches`**

This branch is always rebased directly on `upstream/main` (koush/scrypted).

## Keeping the branch current

When koush merges new commits:

```bash
cd /root/scrypted/fork
git fetch upstream
git rebase upstream/main
git push origin fixes/all-my-patches --force-with-lease
```

Git is configured to rebase on pull for this branch automatically.

## Patches included

| Commit | Fix |
|--------|-----|
| `fix(prebuffer)` | re-mkdir before fallback createWriteStream → prevents ENOENT race |
| `fix(onvif)` | add opt-in RTSP URL Override setting |
| `fix(server)` | drain MessagePort before terminate() in NodeThreadWorker.kill() |
| `feat(server)` | ForkGovernor — per-plugin exit-aware backoff for sdk.fork() |
| `NVR watchdog` | External fork-spawn watchdog + server fork rate limiter |
| `fix(rtsp)` | listenLoop circuit breaker backoff |
| `feat(onvif)` | enhanced event listening and resource management |
| `fix(nvr)` | mkdir + tiered disk-space thresholds (patch script for closed-source NVR) |

## NVR plugin patches (closed-source bundle)

The `@scrypted/nvr` plugin has no public source. Patches are applied to the compiled bundle:

```bash
python3 patches/nvr/patch-nvr.py --check     # verify status
python3 patches/nvr/patch-nvr.py --restart   # apply + restart NVR
```

Re-run after each NVR plugin update.
