# Review: `maker8` `NORMALIZE` Failure With `ffmpeg` Return Code `-9`

## Summary

The problem is most likely **not** a bad Dropbox upload, a render-stage issue, or even a normal FFmpeg validation error.

The failure is happening earlier, inside the `NORMALIZE` stage, and the strongest signal is this:

- `ffmpeg` exited with `returncode = -9`

On Linux, that means the FFmpeg process was terminated by **signal 9 (`SIGKILL`)**.

That usually points to one of these causes:

- the OS or cgroup killed the process because of memory pressure
- the container/runtime killed the process
- an external operator/process sent `SIGKILL`

It does **not** look like a normal command-line or media-format error.

---

## What The Logs Tell Us

For job `28a6c873-bdea-4202-afa6-81978b2e1bb9`, the relevant facts are:

- failure happened in `NORMALIZE`
- failing asset was `yt_1h1VbNeTh7g`
- encoder in the failing subprocess log was `libx264`
- FFmpeg returned `-9`
- stderr tail shows repeated:
  - `frame=0`
  - `time=N/A`
  - `size=0KiB`
  - `speed=N/A`

Interpretation:

- FFmpeg did start
- it did **not** make real forward progress
- it did **not** reach the code timeout path
- it was killed before it could actually encode frames

This is much more consistent with an external kill than with a content/parsing error.

---

## What The Code Does

The failing path is in:

- `src/maker8/pipeline/normalize.py`

### 1. Video normalize can run on GPU or CPU

The main path in `_normalize_video()`:

- checks `check_nvenc()`
- uses `h264_nvenc` if available
- otherwise uses `libx264`

Relevant lines:

- encoder selection in `_normalize_video()`
- fallback to CPU if NVENC path fails

### 2. CPU fallback is explicit

The software fallback path is:

- `_normalize_video_sw()`

This path:

- builds a CPU FFmpeg command
- runs with `libx264`
- treats `CalledProcessError` as `FFMPEG_ERROR`
- treats `TimeoutExpired` as `FFMPEG_TIMEOUT`

### 3. This was not the timeout branch

If the code-level timeout had fired, the logs would show:

- `reason="timeout"`
- `error_code = FFMPEG_TIMEOUT`

That did **not** happen.

Instead, the code hit the `CalledProcessError` branch and logged:

- `returncode = -9`
- `error_code = FFMPEG_ERROR`

That means Python did not kill FFmpeg via its timeout mechanism.

---

## Why `-9` Matters

`returncode = -9` is the key signal here.

Normal FFmpeg content/format/option errors usually look like:

- return code `1`
- stderr containing a concrete decode / option / mux / codec message

That is **not** what we have.

What we have instead is:

- long enough runtime to show the process was alive
- no actual frame progress
- abrupt termination by signal

That is the signature of:

- OOM killer
- cgroup/container kill
- host/process manager kill

This is why the problem most likely lies in **runtime resources or process management**, not in the specific media file syntax.

---

## Why The Encoder Being `libx264` Is Important

The failure log says:

- `encoder = "libx264"`

That means the failing transcode was running on the **CPU path**.

This narrows the diagnosis:

### Case A: GPU was unavailable from the start

`check_nvenc()` may have returned `False`, so `maker8` immediately chose CPU normalization.

Possible reasons:

- container cannot see GPU
- FFmpeg does not expose NVENC
- NVIDIA runtime is missing
- deployment did not grant GPU access

### Case B: GPU path failed first, then CPU fallback also failed

If NVENC was attempted first and failed, `_normalize_video()` would log:

- `normalize.nvenc_fallback`

and then call `_normalize_video_sw()`, which logs `encoder="libx264"`.

If that fallback process is what got killed, the final failure log would still show `libx264`.

So to distinguish the two cases, check whether there is a preceding log:

- `normalize.nvenc_fallback`

If yes:

- GPU path failed first
- CPU fallback then got killed

If no:

- normalize likely ran on CPU from the beginning

---

## What This Is Probably *Not*

Based on the evidence, this is probably **not**:

### 1. A Dropbox issue

The pipeline failed in `NORMALIZE`, long before upload.

### 2. A render-stage issue

The job never reached `RENDER`.

### 3. The 600-second timeout

The code would have emitted `FFMPEG_TIMEOUT`, but it emitted `FFMPEG_ERROR`.

### 4. A simple malformed FFmpeg command

A bad command normally fails quickly with return code `1` and clear stderr.

### 5. A normal unsupported-media error

Unsupported/corrupt input usually yields a concrete decode error, not `SIGKILL`.

---

## Most Likely Root Cause

The most likely root cause is:

> the FFmpeg normalization subprocess was killed externally while running on the CPU path, most likely because the environment ran out of resources or the container/process manager terminated it.

In practical terms, the highest-probability explanation is:

- `maker8` did not normalize this asset on GPU
- CPU transcoding was used instead
- the transcode consumed enough resources to trigger a kill

This matches the earlier operational concern that the system is still falling back to CPU too often.

---

## Secondary Design Problem

There is also a design issue in how the system classifies this failure.

Right now:

- `returncode=-9` becomes `FFMPEG_ERROR`
- `retryable=False`

That is questionable.

Why:

- a `SIGKILL` from OOM/cgroup pressure is often an infrastructure or capacity problem
- it may be transient
- it is not the same class as “invalid codec args” or “bad input file”

This means the current classification may be too coarse and may prevent useful retries or correct operator diagnosis.

---

## What Should Be Checked Next

To confirm the root cause, check:

### 1. Whether GPU was actually unavailable

Look for these logs around the same worker lifetime:

- `gpu.nvenc_probe`
- `normalize.nvenc_fallback`

And verify inside the worker container:

```bash
ffmpeg -hide_banner -encoders | rg nvenc
nvidia-smi
ffmpeg -hide_banner -hwaccels
```

### 2. Whether the host/container OOM-killed FFmpeg

Check:

```bash
dmesg -T | rg -i 'killed process|out of memory|oom'
journalctl -k | rg -i 'killed process|out of memory|oom'
docker inspect <container>
docker stats <container>
```

### 3. Whether container memory limits are too tight

Capture:

- container memory limit
- current RSS usage during normalize
- concurrent workload on the same host

---

## Conclusion

The issue is most likely located in the **runtime/resource layer of the `NORMALIZE` stage**, not in Dropbox and not in the later render pipeline.

The key evidence is:

- `encoder=libx264`
- `returncode=-9`
- `frame=0`
- no timeout error

That combination strongly suggests:

- CPU normalization path
- FFmpeg made no real progress
- the process was killed from outside

The most likely true problem is therefore:

> `maker8` is still using the CPU path for this normalization, and the FFmpeg process is being killed by the environment before it can complete.

