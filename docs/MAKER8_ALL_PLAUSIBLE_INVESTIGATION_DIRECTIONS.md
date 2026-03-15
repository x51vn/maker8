# Instructions: All Plausible Investigation Directions for the Current `maker8` Incident

## Purpose

This document enumerates all realistic investigation directions for the current `maker8` failure pattern:

- `NORMALIZE` failed earlier with `ffmpeg returncode = -9`
- later `RENDER` failed with `moov atom not found`
- the broken file was `yt_1h1VbNeTh7g_norm.mp4`

The goal is to avoid premature conclusions and give the team a complete, structured investigation map.

---

## Current Evidence

What is already known:

- the broken file is the normalized artifact, not the final output video
- `RENDER` failed because `VideoFileClip(...)` could not open `yt_1h1VbNeTh7g_norm.mp4`
- earlier logs showed `NORMALIZE` failing for the same asset with `returncode = -9`
- `-9` means the FFmpeg subprocess was terminated by `SIGKILL`
- the failing normalize log showed `encoder = libx264`, so the CPU path was involved at the failure point

What is not yet proven:

- whether GPU was unavailable from the beginning
- whether NVENC failed first and CPU fallback failed second
- whether the file was corrupted in the same run or reused from an earlier interrupted run
- whether the kill came from OOM, container policy, orchestration, or an operator

---

## Investigation Priorities

Use this order unless new evidence contradicts it:

1. Partial/corrupt normalized artifact reused across runs
2. External kill of FFmpeg during CPU normalization
3. GPU not actually available, forcing CPU path
4. GPU path failed first, CPU fallback then failed
5. Source file itself was incomplete or invalid before normalization
6. Container/runtime resource pressure
7. Filesystem/storage truncation
8. Worker lifecycle interruption or container replacement
9. FFmpeg command/content edge case specific to this media
10. Broader code-level pipeline consistency issues

---

## Direction 1: Reused Partial `_norm.mp4` Artifact

### Why this is plausible

`RENDER` failed on:

- `/tmp/maker8/<job_id>/assets/yt_1h1VbNeTh7g_norm.mp4`

The code in `NormalizeStage._normalize_video()` returns early if the target file already exists:

- `if dest.exists(): return dest`

That means a stale or partial normalized artifact can be reused without validation.

### Why this is high probability

This fits the observed chain very well:

- one run kills FFmpeg during normalize
- partial `_norm.mp4` remains
- later run sees the file exists
- normalize silently accepts it
- render tries to read it
- ffmpeg reports `moov atom not found`

### What to check

- file modification time of `yt_1h1VbNeTh7g_norm.mp4`
- whether its timestamp matches the current run or an earlier run
- whether `normalize.asset.success` was emitted for this asset before `RENDER`
- whether the work directory survived a prior failed run

### Commands

```bash
docker exec maker8-render-worker bash -lc "stat /tmp/maker8/<job_id>/assets/yt_1h1VbNeTh7g_norm.mp4"
docker exec maker8-render-worker bash -lc "ffprobe -hide_banner /tmp/maker8/<job_id>/assets/yt_1h1VbNeTh7g_norm.mp4"
docker logs maker8-render-worker -n 2000 | rg '<job_id>|yt_1h1VbNeTh7g|normalize\\.asset\\.success|subprocess\\.failure'
```

### Code areas

- `src/maker8/pipeline/normalize.py`
- `src/maker8/pipeline/context.py`
- `src/maker8/pipeline/orchestrator.py`

---

## Direction 2: FFmpeg Was Killed Externally During CPU Normalize

### Why this is plausible

`returncode = -9` is not a normal FFmpeg content error.

It strongly suggests:

- OOM killer
- cgroup/container kill
- explicit `kill -9`
- infrastructure termination

### Why this matters

If FFmpeg was killed from outside, the broken `_norm.mp4` is only a symptom.
The real root cause is resource pressure or process management.

### What to check

- kernel OOM logs
- container OOM status
- host memory pressure during the incident window
- container memory limit
- whether another process killed FFmpeg

### Commands

```bash
dmesg -T | rg -i 'killed process|out of memory|oom'
journalctl -k | rg -i 'killed process|out of memory|oom'
docker inspect maker8-render-worker --format '{{json .State}}'
docker stats --no-stream maker8-render-worker
```

### Code areas

- `src/maker8/pipeline/normalize.py`
- `src/maker8/retry.py`

---

## Direction 3: GPU Was Not Actually Available

### Why this is plausible

The failure log showed:

- `encoder = libx264`

That may simply mean:

- NVENC was not available
- container had no GPU access
- ffmpeg inside container had no NVENC support

### What to check

- startup log `gpu.nvenc_probe`
- startup log `gpu capability summary` if present
- whether `ffmpeg -encoders` lists `h264_nvenc`
- whether `nvidia-smi` works in the container
- whether deployment requested GPU access

### Commands

```bash
docker exec maker8-render-worker bash -lc "ffmpeg -hide_banner -encoders | rg nvenc"
docker exec maker8-render-worker bash -lc "ffmpeg -hide_banner -hwaccels"
docker exec maker8-render-worker bash -lc "nvidia-smi"
docker logs maker8-render-worker -n 500 | rg 'gpu\\.nvenc_probe|normalize\\.gpu_probe|encoder'
```

### Code areas

- `src/maker8/rendering/encoder.py`
- `src/maker8/pipeline/normalize.py`
- Docker/deployment config outside the app repo

---

## Direction 4: NVENC Failed First, Then CPU Fallback Failed

### Why this is plausible

`NormalizeStage._normalize_video()` tries GPU first and falls back to CPU on `CalledProcessError`.

If NVENC failed first, then CPU fallback could still be the final failure recorded.

### What to check

- presence of `normalize.nvenc_fallback`
- whether the same asset had two normalize attempts in the same run
- stderr from the NVENC failure

### Commands

```bash
docker logs maker8-render-worker -n 2000 | rg '<job_id>|yt_1h1VbNeTh7g|normalize\\.nvenc_fallback|encoder'
```

### Code areas

- `src/maker8/pipeline/normalize.py`

---

## Direction 5: Downloaded Source Asset Was Already Invalid

### Why this is plausible

The downloaded YouTube file may have been incomplete or malformed before normalize began.

Examples:

- interrupted or partial download
- muxed result invalid
- source file missing proper metadata

### Why this is lower probability than Direction 1 or 2

If the source were invalid, FFmpeg often returns a normal non-zero exit code with a more explicit decode error, not necessarily `-9`.

Still, it must be ruled out.

### What to check

- validity of the original downloaded file before normalization
- whether the original `yt_1h1VbNeTh7g.mp4` can be opened by ffprobe
- file size compared with expected size

### Commands

```bash
docker exec maker8-render-worker bash -lc "ffprobe -hide_banner /tmp/maker8/<job_id>/assets/yt_1h1VbNeTh7g.mp4"
docker exec maker8-render-worker bash -lc "stat /tmp/maker8/<job_id>/assets/yt_1h1VbNeTh7g.mp4"
```

### Code areas

- `src/maker8/plugins/sources/youtube.py`
- `src/maker8/pipeline/download.py`

---

## Direction 6: Container/Host Resource Exhaustion

### Why this is plausible

CPU normalization with `libx264` can be expensive.
If the worker host is under pressure, the normalize subprocess may be the victim.

### What to check

- total host RAM
- other heavy containers on the same host
- per-container memory constraints
- CPU steal/load during incident
- disk I/O pressure

### Commands

```bash
free -h
uptime
docker stats --no-stream
iostat -xz 1 3
df -h
df -ih
```

### Code areas

- none directly; this is mainly an infrastructure investigation

---

## Direction 7: Filesystem or Disk Problem Caused Truncation

### Why this is plausible

If disk fills up or I/O fails during transcode, FFmpeg may leave a broken MP4.

### What to check

- available disk space
- inode exhaustion
- filesystem errors
- whether output file size is suspiciously small or zero

### Commands

```bash
df -h
df -ih
dmesg -T | rg -i 'ext4|xfs|i/o error|blk_update'
```

### Code areas

- `src/maker8/pipeline/normalize.py`
- `src/maker8/pipeline/orchestrator.py`

---

## Direction 8: Worker or Container Was Interrupted Mid-Stage

### Why this is plausible

If the container was replaced, killed, or redeployed mid-normalize, the job directory may contain partial artifacts.

### What to check

- container start time
- restart count
- deploy/recreate events near the incident time
- whether cleanup ran

### Commands

```bash
docker inspect maker8-render-worker --format '{{.State.StartedAt}} restart={{.RestartCount}} status={{.State.Status}}'
docker events --since 2h | rg 'maker8-render-worker'
```

### Code areas

- `src/maker8/app.py`
- `src/maker8/pipeline/orchestrator.py`

---

## Direction 9: Cleanup Did Not Remove a Broken Work Directory

### Why this is plausible

The orchestrator does best-effort cleanup with `shutil.rmtree(...)`.
If cleanup fails or the process dies before cleanup, partial files can survive.

### What to check

- presence of `orchestrator.cleanup_error`
- whether the work dir existed before the current attempt
- whether failed jobs leave residual files behind

### Commands

```bash
docker logs maker8-render-worker -n 1000 | rg 'cleanup'
docker exec maker8-render-worker bash -lc "find /tmp/maker8 -maxdepth 2 -type d | sort"
```

### Code areas

- `src/maker8/pipeline/orchestrator.py`

---

## Direction 10: Normalize Success Criteria Are Too Weak

### Why this is plausible

Today, normalize success effectively means:

- subprocess returned without raising
- or file already existed

It does not verify:

- file is a valid MP4
- file has a readable `moov atom`
- file can actually be opened by ffprobe

### What to check

- whether successful normalize logs can coexist with unreadable outputs
- whether any `_norm.mp4` files in current/previous jobs are invalid

### Code areas

- `src/maker8/pipeline/normalize.py`

### Required validation if this direction is confirmed

After normalize completes, run a cheap validation such as:

- `ffprobe`
- or a dedicated `is_valid_media_file()` helper

---

## Direction 11: Render Stage Lacks Defensive Validation

### Why this is plausible

`RENDER` trusts `ctx.asset_path(asset_id)` and immediately builds `VideoFileClip`.
It does not validate normalized assets before the scene graph is built.

### Why this matters

This does not create the root problem, but it makes downstream failure noisy and late.

### What to check

- whether `RENDER` should reject invalid normalized assets earlier
- whether a bad normalized artifact can be detected before MoviePy starts

### Code areas

- `src/maker8/pipeline/render.py`
- `src/maker8/rendering/layers.py`

---

## Direction 12: Specific FFmpeg/Media Edge Case

### Why this is plausible

Some source files expose bugs or pathological behavior in:

- demuxing
- decode startup
- timestamp handling
- H.264 bitstream parsing

### Why this is lower priority

The `-9` signal still makes an external kill more likely than a clean codec error.

### What to check

- manually run the exact FFmpeg normalize command on the same input
- test with and without audio
- test with a simplified command

### Commands

```bash
ffmpeg -y -i yt_1h1VbNeTh7g.mp4 -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 192k -movflags +faststart out.mp4
ffmpeg -y -i yt_1h1VbNeTh7g.mp4 -an -c:v libx264 -preset fast -crf 23 out_no_audio.mp4
ffprobe -hide_banner yt_1h1VbNeTh7g.mp4
```

---

## Direction 13: Retry/Failure Classification Is Too Coarse

### Why this is plausible

The current error classification turns many distinct failure modes into:

- `FFMPEG_ERROR`

That hides the difference between:

- invalid input
- external SIGKILL
- OOM
- infrastructure interruption

### What to check

- whether `returncode=-9` should become a dedicated error code
- whether this class should be retryable in some environments

### Code areas

- `src/maker8/pipeline/normalize.py`
- `src/maker8/retry.py`

---

## Direction 14: Observability Gaps Are Preventing Root Cause Confirmation

### Why this is plausible

The current logs are better than before, but still incomplete for this incident.

Missing or weak signals include:

- whether `_norm.mp4` existed before normalize started
- output file size immediately after failure
- ffprobe validation result
- whether cleanup removed the broken file
- whether host/container memory pressure was present

### What to check

- whether a follow-up code change is needed even after root cause is known

### Code areas

- `src/maker8/pipeline/normalize.py`
- `src/maker8/pipeline/render.py`
- `src/maker8/pipeline/orchestrator.py`
- `src/maker8/observability/*`

---

## Evidence Collection Checklist

Before rerunning or cleaning up, collect:

```bash
docker logs maker8-render-worker -n 3000 | rg '<job_id>|yt_1h1VbNeTh7g|normalize|render|cleanup|gpu'
docker exec maker8-render-worker bash -lc "stat /tmp/maker8/<job_id>/assets/yt_1h1VbNeTh7g.mp4"
docker exec maker8-render-worker bash -lc "stat /tmp/maker8/<job_id>/assets/yt_1h1VbNeTh7g_norm.mp4"
docker exec maker8-render-worker bash -lc "ffprobe -hide_banner /tmp/maker8/<job_id>/assets/yt_1h1VbNeTh7g.mp4"
docker exec maker8-render-worker bash -lc "ffprobe -hide_banner /tmp/maker8/<job_id>/assets/yt_1h1VbNeTh7g_norm.mp4"
docker inspect maker8-render-worker --format '{{json .State}}'
dmesg -T | rg -i 'oom|killed process'
```

---

## Most Likely Combined Story

At the moment, the most coherent explanation is:

1. `maker8` normalized `yt_1h1VbNeTh7g` on the CPU path
2. FFmpeg was killed externally during that normalize attempt
3. a partial `_norm.mp4` artifact remained
4. a later path reused or encountered that corrupted artifact
5. `RENDER` then failed immediately with `moov atom not found`

This is the leading hypothesis, but it still needs runtime evidence to be treated as proven.

---

## Definition of Done

The investigation is complete only when the team can answer all of these:

1. Why was `encoder=libx264` used for this asset?
2. Why did FFmpeg receive `SIGKILL`?
3. Was the broken `_norm.mp4` produced in the same run or reused from a previous run?
4. Why did the pipeline allow a corrupt normalized artifact to reach `RENDER`?
5. What concrete code or deployment change prevents recurrence?

