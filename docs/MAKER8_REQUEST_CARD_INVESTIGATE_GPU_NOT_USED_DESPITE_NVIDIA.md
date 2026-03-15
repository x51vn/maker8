# Request Card: Investigate Why `maker8` Is Not Using GPU Consistently Despite NVIDIA Being Available

## Problem Statement

`maker8` is running on a machine that does have an NVIDIA GPU, but the pipeline is still falling back to CPU in incidents where GPU should be available.

This needs a detailed investigation and a durable fix.

The key issue is no longer:

> "Does the machine have an NVIDIA GPU?"

The real issue is:

> "Why does `maker8` still behave as if GPU is unavailable or unusable in parts of the pipeline, even though runtime GPU capability is present?"

---

## Evidence Already Collected

The current runtime already shows strong evidence that GPU capability exists inside the container:

### Container/runtime evidence

- `nvidia-smi` works inside `maker8-render-worker`
- `ffmpeg -encoders` exposes:
  - `h264_nvenc`
  - `hevc_nvenc`
  - `av1_nvenc`
- `ffmpeg -hwaccels` shows:
  - `cuda`
- container env includes:
  - `NVIDIA_VISIBLE_DEVICES=all`
  - `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`
- Docker inspect shows a GPU `DeviceRequests` entry

### App startup evidence

Recent worker startup logs already show:

- `gpu.nvenc_probe nvenc_available=true`
- `app.gpu_capabilities nvidia_smi=true nvenc_available=true cuda_hwaccel=true gpu_render_enabled=true`

### Contradictory pipeline evidence

Despite that, incident logs still show:

- `NORMALIZE` failing with `encoder="libx264"`
- CPU path being used during an asset failure

That contradiction is the real problem.

---

## Why This Needs Investigation

If GPU is truly available, then CPU fallback should happen only in narrow, explainable cases such as:

- explicit CPU-only request
- media-specific GPU decode/encode incompatibility
- well-logged NVENC failure followed by safe fallback

But the current operational picture is inconsistent:

- startup claims GPU is available
- runtime still ends up on CPU in failure cases
- operators cannot clearly tell whether this is:
  - expected fallback
  - broken configuration
  - code-path inconsistency
  - stale build
  - invalid retry artifact reuse

This inconsistency must be explained and eliminated.

---

## Investigation Goals

The investigation must determine:

1. whether `maker8` is actually choosing GPU by default for both `NORMALIZE` and `RENDER`
2. when and why it falls back to CPU
3. whether the CPU fallback is correct, accidental, or caused by stale/corrupt artifacts
4. whether different worker versions or deployment paths behave differently
5. what changes are needed so GPU usage becomes predictable, observable, and reliable

---

## Required Investigation Areas

## 1. Confirm Which Code Version Is Actually Running

We need to rule out version drift first.

The codebase now contains GPU-aware startup probing and encoder selection, but incidents may still come from:

- an older image
- a worker running stale package code
- a mixed fleet with different behavior

### Required checks

- image tag deployed to `maker8-render-worker`
- package version inside the container
- exact source snapshot corresponding to the running image
- whether all worker instances are on the same build

### Key question

Are the failing logs coming from the same code version that currently logs:

- `gpu.nvenc_probe`
- `app.gpu_capabilities`

or from an older build?

---

## 2. Investigate Why `NORMALIZE` Used `libx264` Even Though NVENC Was Available

This is the core contradiction.

If startup says:

- `nvenc_available=true`

then a later normalize step should not quietly land on CPU unless one of the following happened:

- GPU path was never selected
- GPU path was selected and failed
- an older code path bypassed the GPU selection
- the artifact was reused from a previous CPU fallback run

### Required checks

- whether `normalize.nvenc_fallback` was emitted for the failing asset
- whether `subprocess.start` for that asset showed `encoder="h264_nvenc"` first
- whether the asset ever entered GPU normalize before CPU fallback
- whether the failing `libx264` log belongs to `_normalize_video_sw()` fallback
- whether the code reused an already-existing `_norm.mp4`

### Key question

Did the system intentionally fall back from GPU to CPU, or did it incorrectly skip GPU in the first place?

---

## 3. Investigate Artifact Reuse Across Retries

Recent logs strongly suggest this is part of the problem.

Observed pattern:

- attempt 1: normalize failed
- attempt 2: normalize completed suspiciously fast
- later render failed opening `*_norm.mp4`

That pattern is highly suspicious for:

- a stale partial file being reused only because it exists

### Required checks

- whether `_normalize_video()` returns early on `dest.exists()`
- whether existing normalized files are validated before reuse
- whether a partial file from a killed FFmpeg process survives retry
- whether failed normalize paths always delete partial outputs

### Key question

Is the system blaming “GPU unavailable” when the real problem is reuse of a stale CPU-generated partial artifact?

---

## 4. Investigate Whether GPU Path Fails on Specific Media

It is possible that:

- GPU capability exists generally
- but some source files fail under the NVENC/CUDA path
- and the system falls back to CPU for those assets

If this is happening, it must be visible and explainable.

### Required checks

- run the exact failing asset through GPU normalize manually
- capture stderr from NVENC failure, if any
- test whether failure is on:
  - decode
  - hwaccel
  - encode init
  - muxing

### Key question

Is the asset itself causing a GPU-specific failure that makes CPU fallback legitimate?

---

## 5. Investigate Deployment Runtime Consistency

Docker inspect currently shows:

- GPU `DeviceRequests` present
- but `HostConfig.Runtime = "runc"`

This may still work on a correctly configured host, but it is operationally ambiguous.

### Required checks

- exact Docker/NVIDIA Container Toolkit setup on the host
- whether GPU access is consistently granted across restarts and deployments
- whether deployment config explicitly requests GPU in the supported way
- whether all worker launches use the same runtime pattern

### Key question

Is GPU visibility stable and intentional, or merely happening “by luck” under the current host setup?

---

## 6. Investigate Runtime Resource Pressure on CPU Fallback

Even if GPU exists, CPU fallback still needs to be safe.

The observed `SIGKILL` on CPU normalize suggests:

- resource pressure
- OOM/cgroup kill
- infrastructure kill during fallback

### Required checks

- host RAM and container memory limits
- kernel OOM logs
- whether CPU fallback is too expensive for current worker sizing
- whether fallback should be retried differently

### Key question

Is the real production issue that GPU path is sometimes bypassed, and CPU fallback is too fragile to handle the workload?

---

## 7. Investigate Inconsistent GPU Use Across Pipeline Stages

The codebase now treats stages differently:

- `NORMALIZE` has explicit GPU/CPU branching
- `RENDER` has newer encoder resolution logic
- `MoviePy` composition is still partially CPU-driven

That means “GPU enabled” does not automatically mean “everything uses GPU”.

### Required checks

- which parts of `NORMALIZE` use GPU
- which parts of `RENDER` use GPU
- whether final encode is GPU-backed but clip composition remains CPU-bound
- whether operator expectations are ahead of actual architecture

### Key question

Is the system failing because the team expects full GPU acceleration while only a subset of work is actually GPU-enabled?

---

## 8. Investigate Logging and Observability Gaps

Even with current improvements, operators still cannot always answer:

- did GPU path start?
- did GPU path fail?
- why did CPU fallback begin?
- which artifact was reused?

### Required checks

- whether every GPU-to-CPU transition is explicitly logged
- whether file reuse is logged
- whether startup capability logs are emitted on every worker boot
- whether render/normalize logs clearly show selected encoder and fallback cause

### Key question

Are we diagnosing “GPU unavailable” partly because the logs are still too ambiguous?

---

## Required Deliverables

This investigation must produce:

1. A clear explanation of why GPU-capable runtime still led to CPU normalize in the failing incident.
2. A classification of the CPU path trigger:
   - skipped GPU
   - legitimate GPU failure and fallback
   - stale artifact reuse
   - stale deployment/build
   - runtime misconfiguration
3. Evidence showing whether the issue is code, deployment, or both.
4. A concrete fix plan.
5. A verification checklist proving GPU is used consistently after the fix.

---

## Required Fix Outcomes

The final solution should ensure all of the following:

### 1. GPU usage is deterministic

If GPU is available, `maker8` should use it by default for eligible stages.

### 2. CPU fallback is explicit

If fallback happens, logs must state:

- why fallback happened
- which encoder failed
- which encoder is now being used

### 3. Partial artifacts are never silently reused

Broken `_norm.mp4` files must not survive as valid inputs to later stages.

### 4. Deployment is standardized

The worker deployment must expose GPU intentionally and verifiably, not implicitly.

### 5. Verification is operationally simple

Operators must be able to answer in minutes:

- is GPU visible?
- is GPU being chosen?
- did GPU fail?
- did CPU fallback begin?

---

## Definition of Done

This request is complete only when the team can show:

1. GPU is visible inside the worker container.
2. `maker8` startup logs confirm GPU capability.
3. `NORMALIZE` and `RENDER` choose GPU by default when appropriate.
4. Any fallback to CPU is explicitly explained in logs.
5. Corrupt partial normalized artifacts cannot be reused.
6. A representative render job proves that GPU is used consistently in production.

Until then, the system should be considered:

> GPU-capable at runtime, but not yet GPU-reliable in pipeline behavior.

