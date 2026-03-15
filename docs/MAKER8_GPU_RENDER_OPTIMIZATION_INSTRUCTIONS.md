# Instructions: Optimize `maker8` Video Rendering and Enable GPU Acceleration

## Summary

`maker8` needs a deliberate GPU-enabled render path.

Right now, the system is only partially GPU-aware:

- `NORMALIZE` already probes NVENC and can use `h264_nvenc`
- `RENDER` still follows the output codec from the contract
- the canonical default output codec is still `libx264`
- the current container image is based on `python:3.11-slim` with generic `ffmpeg`

This means the final render stage is still very likely running on CPU unless an upstream request explicitly overrides the output codec.

That is not enough for a production render worker where:

- video composition is long-running
- final encode is expensive
- operator expectation is that GPU should be used by default when available

This work should optimize the render pipeline and make GPU usage an intentional, verifiable system capability.

---

## Current State

### 1. Final render still defaults to CPU encoding

The canonical contract currently defines:

- `OutputConfig.codec = "libx264"`
- `OutputConfig.preset = "medium"`

in:

- `src/render_contracts/render_spec.py`

The final render path then forwards that codec directly into MoviePy:

- `src/maker8/rendering/composer.py`

That means the final encode still defaults to CPU unless the request overrides the codec.

### 2. `NORMALIZE` already has GPU logic

`src/maker8/pipeline/normalize.py` already:

- probes whether `h264_nvenc` exists
- uses CUDA/NVENC when available
- falls back to `libx264` if GPU encode fails

So the codebase already accepts the idea of:

- capability detection
- GPU-first execution
- software fallback

The render stage should follow the same operational model.

### 3. Container runtime is not GPU-oriented by default

The current `Dockerfile`:

- uses `python:3.11-slim`
- installs distro `ffmpeg`
- does not establish a CUDA runtime
- does not guarantee NVENC-capable ffmpeg
- does not guarantee container access to host GPU devices

Even if the host has an NVIDIA GPU, the current image/runtime may still render entirely on CPU.

### 4. MoviePy limits how much acceleration we get

This is important:

- GPU encoding can accelerate the final output encode
- but MoviePy composition is still Python-driven and often CPU-heavy

So enabling NVENC will improve throughput, but it will not magically move the whole scene graph and compositing pipeline to GPU.

This work should still be done, but expectations must be realistic.

---

## Goals

The system should be improved so that:

1. final render uses GPU encoding by default when GPU is available
2. fallback to CPU remains safe and automatic
3. container/deployment is explicitly GPU-capable
4. logs prove which encoder path was used
5. the team can measure whether render time actually improved

---

## Required Changes

## 1. Add GPU-aware encoder selection for the `RENDER` stage

Implement the same capability model used in `NORMALIZE` for final render.

### Required behavior

At render time, the system should:

- probe whether NVENC is available
- choose a GPU encoder by default when available
- fall back to CPU encoding when GPU encode is unavailable or fails
- log the chosen encoder explicitly

### Minimum implementation requirement

Introduce a render encoder selection layer in `src/maker8/rendering/composer.py` or a small helper module.

It should decide between:

- `h264_nvenc` for GPU
- `libx264` for CPU fallback

Preferred rule:

- if request codec is explicitly set and valid, honor it
- otherwise choose `h264_nvenc` when available
- otherwise use `libx264`

### Important note

Do not rely on upstream callers to set `codec="h264_nvenc"`.

`maker8` must make the optimal choice itself.

---

## 2. Update the contract defaults so GPU-capable systems are not CPU-first by accident

The current canonical default:

- `codec = "libx264"`

encodes an assumption that CPU is the normal path.

That assumption is no longer appropriate if the target deployment is GPU-backed.

### Required action

Review and update the render output policy in:

- `src/render_contracts/render_spec.py`

Possible acceptable strategies:

### Strategy A: keep wire-format default conservative, choose GPU at runtime

Keep:

- `codec = "libx264"`

but document that `maker8` may override it with a GPU encoder when the request does not explicitly pin a codec.

This is the safer compatibility strategy.

### Strategy B: introduce an explicit `auto` policy

Add a codec mode such as:

- `codec = "auto"`

Then let `maker8` resolve:

- `auto -> h264_nvenc` when available
- `auto -> libx264` otherwise

This is cleaner operationally, but it requires contract coordination across `editor8` and `maker8`.

Preferred direction:

- use `auto` or equivalent runtime policy instead of hard-coding CPU defaults forever

---

## 3. Pass GPU-specific ffmpeg parameters for final encode

Using `h264_nvenc` is not enough by itself.

The final render path should use GPU-appropriate settings such as:

- `-preset p4` or similar NVENC preset
- `-cq` / `-rc:v vbr` or another defined quality policy
- `-pix_fmt yuv420p`
- `-movflags +faststart`

### Required action

Refactor final output configuration so GPU and CPU encoders have separate tuned settings.

Example direction:

- CPU:
  - `codec=libx264`
  - `preset=medium` or `fast`
  - `crf=23`
- GPU:
  - `codec=h264_nvenc`
  - `preset=p4`
  - `cq=23`
  - optional `rc=vbr`

Do not treat CPU and GPU presets as interchangeable.

---

## 4. Add explicit render-path logging

The system must prove at runtime which path it actually used.

### Required logs

At render start, log:

- `job_id`
- selected encoder
- selected preset
- whether GPU path was selected
- whether fallback policy is enabled

On fallback, log:

- GPU render attempt failed
- reason
- fallback to CPU started

At render completion, log:

- encoder used
- render duration
- output size

Without these logs, operators will continue guessing whether GPU is used.

---

## 5. Make the container image GPU-capable

The application code alone is not enough.

If the container does not have access to a GPU-capable ffmpeg and NVIDIA runtime, the render will still use CPU.

### Required container/runtime work

The deployment path must ensure all of the following:

1. host has NVIDIA driver installed
2. host has `nvidia-container-toolkit`
3. container is started with GPU access
4. ffmpeg inside the container supports `h264_nvenc`
5. the container can see `/dev/nvidia*`

### Required verification inside the container

These commands must succeed:

```bash
nvidia-smi
ffmpeg -hide_banner -encoders | rg nvenc
ffmpeg -hide_banner -hwaccels
```

If these fail, the deployment is not GPU-ready regardless of code changes.

---

## 6. Update deployment to request GPU explicitly

The worker deployment must run with GPU access on purpose, not by accident.

### Required deployment behavior

The render worker container must be launched with:

- `--gpus all`

or the equivalent Compose configuration.

For Compose-based deployment, add the appropriate GPU reservation/device request for the worker service.

If the actual deployment environment uses a separate deployment repo, the GPU requirement must be codified there and documented as mandatory.

### Also require

- `NVIDIA_VISIBLE_DEVICES=all`
- `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`

when relevant to the chosen runtime setup.

---

## 7. Add startup capability checks

At app startup, `maker8` should log a concise GPU capability summary.

### Required startup logs

Log fields should include:

- whether `nvidia-smi` is available
- whether ffmpeg exposes `h264_nvenc`
- whether CUDA hwaccel is visible
- whether render GPU path is enabled
- whether normalize GPU path is enabled

This should appear once at startup so operators immediately know whether the worker is truly GPU-capable.

---

## 8. Add runtime fallback behavior

GPU acceleration must not make the worker brittle.

### Required behavior

If GPU render fails because:

- NVENC session is unavailable
- encoder init fails
- GPU device is busy
- driver/runtime mismatch occurs

then the worker should:

- log the GPU failure clearly
- retry the same final encode on CPU
- continue the job instead of failing immediately, when safe to do so

This should mirror the current design in `NORMALIZE`.

---

## 9. Measure actual render improvement

Do not declare success just because the code mentions NVENC.

### Required benchmark

Use a representative sample job and compare:

- CPU final render duration
- GPU final render duration
- total pipeline duration
- output bitrate/quality characteristics
- GPU utilization

Record:

- input duration
- number of scenes
- asset mix
- render duration
- encoder used

This is required to confirm whether the optimization is meaningful.

---

## 10. Be explicit about the architectural limit of MoviePy

This project should document a key reality:

- GPU encode accelerates the final output stage
- it does not fully eliminate CPU-heavy composition inside MoviePy

### Required note in the implementation plan

If the team needs substantially more acceleration beyond NVENC output encoding, a later phase should evaluate:

- direct `ffmpeg` filter graph composition
- PyAV-based rendering
- a GPU-native video processing path

This is a later optimization phase, not a blocker for enabling GPU encode now.

---

## Suggested Implementation Order

1. Add startup capability logging.
2. Add encoder selection helper for final render.
3. Add GPU-first final encode with CPU fallback.
4. Tune ffmpeg params separately for CPU and GPU.
5. Update Docker/deployment to expose GPU.
6. Verify `nvidia-smi` and `ffmpeg ... nvenc` inside the running container.
7. Benchmark before/after.

---

## Definition of Done

This work is done only when all of the following are true:

1. `maker8` logs clearly show whether `RENDER` used GPU or CPU.
2. final render uses `h264_nvenc` automatically when GPU is available.
3. final render falls back safely to CPU if GPU encode fails.
4. the worker container can prove GPU access with runtime checks.
5. deployment configuration explicitly enables GPU for the render worker.
6. benchmark evidence shows improved render time for representative jobs.

Until then, the system should still be considered CPU-first in practice, even if some parts of the pipeline mention GPU.

