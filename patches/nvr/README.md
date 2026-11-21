# Scrypted NVR Plugin Patches

These patches target `@scrypted/nvr` (closed-source npm package).
They are applied as string replacements to the compiled `main.nodejs.js` bundle
using the `patch-nvr.py` script in this directory.

## Applied to version
Package: `@scrypted/nvr`  
Applied bundle: `main.nodejs.js`

---

## Fix 1 — mkdir before writeFile (session metadata)

**Problem:** `Wa()` writes session JSON without ensuring the parent directory exists,
causing `ENOENT` crashes when the recording directory is new.

**Severity:** High — causes NVR to crash on new recordings.

---

## Fix 2 — mkdir before writeFile (thumbnail cache)

**Problem:** `fetchRecordingStreamThumbnail()` writes a JPEG thumbnail without
ensuring the cache directory exists, causing `ENOENT` crashes.

**Severity:** High — crashes the thumbnail serving path on new segments.

---

## Fix 3 — Tiered disk-space thresholds (pruner / truncation / recording stop)

**Problem:** The pruner and the recording-stop check used the same (or very close)
disk-space thresholds, leaving no lead time for the pruner to actually free space
before recordings are refused. With many cameras, the disk can fill faster than the
pruner can react.

**Root cause:**
- Pruner fired at: `freePct < 15% OR freeGb < computeMinFreeSpaceGb()*1`
- Recording stop fired at: `freePct < 5%` (absolute, no GB floor)
- Gap: only 10% + no GB safety margin

**Fix — 3-tier system:**

| Tier | Component | Condition |
|------|-----------|-----------|
| 1 (first) | Pruner `gc()` | `freePct < 20%` OR `freeGb < minFreeGb×2` |
| 2 | Stream truncation | `freePct < 20%` OR `freeGb < minFreeGb×1` |
| 3 (last resort) | Recording stop | `freePct < 10%` OR `freeGb < minFreeGb×0.5` |

Where `computeMinFreeSpaceGb() = 10 + 2 × numCameras` (e.g. 10 cameras → 30 GB floor).

**Gap between tier 1 and tier 3:**  
10% of disk + 1.5 × minFreeGb — scales with total disk size and camera count.
