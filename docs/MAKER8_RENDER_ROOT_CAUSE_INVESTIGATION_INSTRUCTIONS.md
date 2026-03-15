# Instructions: Find the Real Root Cause of the Current `maker8` Render Incident

## Summary

The current incident must **not** be treated as a Dropbox upload failure yet.

The strongest evidence so far says:

- `VALIDATE`, `RESOLVE_ASSETS`, `DOWNLOAD`, `NORMALIZE`, and `TTS` all completed
- `RENDER` started
- `maker8.rendering.composer` logged `composer.write`
- there is still **no** `render.success`
- there is still **no** `stage.success` for `RENDER`
- there is still **no** `upload.start`

That means the pipeline has **not yet proven** that it reached `UPLOAD_DROPBOX`.

The current primary investigation target is the render boundary around:

- `compose_video()` in `src/maker8/rendering/composer.py`
- `final.write_videofile(...)` in `src/maker8/rendering/composer.py`
- the `RENDER` stage wrapper in `src/maker8/pipeline/render.py`

The goal of this investigation is to identify the **actual blocking or failing mechanism**, not the last subsystem people suspect.

---

## Current Facts

For job `29aaa64d-09a5-4112-9437-5f1ef19e3e15`, the logs currently show:

- `TTS` completed successfully
- `RENDER` started
- `render.start` was emitted
- `composer.write` was emitted for `/tmp/maker8/29aaa64d-09a5-4112-9437-5f1ef19e3e15/output/29aaa64d-09a5-4112-9437-5f1ef19e3e15.mp4`

But there is **no evidence yet** of:

- `render.success`
- `render.failure`
- `stage.success` for `RENDER`
- `stage.failure` for `RENDER`
- `upload.start`
- `dropbox.upload.start`

Dropbox credentials also appear valid at startup:

- `dropbox.auth_validated` was logged

So the most defensible current statement is:

> the job is either still encoding, stalled inside `write_videofile()`, or the process exited unexpectedly before the render stage completed.

It is **not yet defensible** to conclude that Dropbox upload is the root cause.

---

## What Must Be Proven

The investigation must end with one of these concrete conclusions:

1. `write_videofile()` was still actively encoding and just slow.
2. `write_videofile()` stalled/hung and stopped making forward progress.
3. `ffmpeg` failed during render, but the current logging did not surface the failure.
4. the worker process/container was replaced or interrupted between render start and upload.
5. render completed and upload failed afterward.

Anything weaker than one of the above is still only hypothesis.

---

## Investigation Rules

### 1. Do not conclude Dropbox failure without upload logs

Do **not** call this a Dropbox incident unless logs show at least:

- `upload.start`
- or `dropbox.upload.start`
- or an explicit upload exception

### 2. Do not close the incident on inference alone

The final conclusion must be based on at least one of:

- runtime process evidence
- output file growth evidence
- actual `ffmpeg` stderr
- actual `MoviePy` progress output
- container lifecycle evidence

### 3. Treat the healthcheck issue as a separate problem

The container is currently `unhealthy` because deployment still checks the old file path, while the app now writes:

- `/tmp/maker8_live`
- `/tmp/maker8_ready`

This mismatch is real and must be fixed, but it is **not automatically** the cause of this specific stuck render unless container replacement or orchestration behavior proves it.

---

## Step 1: Reconstruct the Exact Timeline

Collect and preserve logs for the specific job:

```bash
docker logs maker8-render-worker -n 2000 | rg '29aaa64d-09a5-4112-9437-5f1ef19e3e15|render\.|composer\.write|upload\.|dropbox\.upload|stage\.'
```

The timeline must explicitly list:

- time `RENDER` started
- time `composer.write` was logged
- whether any further log was emitted afterward
- whether the worker restarted or was replaced during the incident window

Also capture container lifecycle:

```bash
docker inspect maker8-render-worker --format '{{.State.StartedAt}} restart={{.RestartCount}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}}'
```

If orchestration recreated the container during or right after `RENDER`, that is a major lead.

---

## Step 2: Determine Whether Rendering Is Still Making Progress

The next question is simple:

> Is `MoviePy/ffmpeg` still actively writing the file, or is it stuck?

Check the output file repeatedly:

```bash
docker exec maker8-render-worker bash -lc 'ls -lh /tmp/maker8/29aaa64d-09a5-4112-9437-5f1ef19e3e15/output'
docker exec maker8-render-worker bash -lc 'stat /tmp/maker8/29aaa64d-09a5-4112-9437-5f1ef19e3e15/output/29aaa64d-09a5-4112-9437-5f1ef19e3e15.mp4'
```

Then repeat every few seconds:

```bash
watch -n 5 "docker exec maker8-render-worker bash -lc 'stat -c \"%s %y\" /tmp/maker8/29aaa64d-09a5-4112-9437-5f1ef19e3e15/output/29aaa64d-09a5-4112-9437-5f1ef19e3e15.mp4'"
```

Interpretation:

- if file size keeps increasing, render is still progressing
- if file size stays flat for a long time, render is likely stalled
- if the file disappears or stops updating after a container event, suspect process interruption

---

## Step 3: Inspect the Live Process Tree

Check whether `ffmpeg` is still running inside the worker container:

```bash
docker exec maker8-render-worker bash -lc "ps -ef | rg 'ffmpeg|python|maker8'"
```

If available, also inspect resource usage:

```bash
docker exec maker8-render-worker bash -lc "top -b -n 1 | head -40"
docker exec maker8-render-worker bash -lc "nvidia-smi"
```

Interpretation:

- `ffmpeg` running + CPU/GPU active + file growing:
  render is still in progress
- `ffmpeg` running + no file growth:
  likely stuck or blocked
- no `ffmpeg` child but Python still running inside `RENDER`:
  likely blocked in `MoviePy` orchestration or clip graph handling
- neither `ffmpeg` nor expected Python activity:
  suspect worker interruption or process exit

---

## Step 4: Inspect the Output Artifact Directly

If the output file exists, probe it:

```bash
docker exec maker8-render-worker bash -lc "ffprobe -hide_banner /tmp/maker8/29aaa64d-09a5-4112-9437-5f1ef19e3e15/output/29aaa64d-09a5-4112-9437-5f1ef19e3e15.mp4"
```

This helps answer:

- is the MP4 structurally valid already?
- is it partially written and playable?
- did ffmpeg leave a broken/incomplete artifact?

If the file is valid and complete but logs never advanced, that points to a missing log boundary or a hang after encode completion.

---

## Step 5: Instrument `RENDER` So It Can No Longer Go Silent

The current code is too opaque during render:

- `composer.write` logs **before** encoding starts
- `write_videofile()` uses `logger=None`
- there is no render timeout

Temporary instrumentation must be added if the issue reproduces:

### A. Emit explicit logs around `write_videofile()`

Add logs for:

- `composer.write.start`
- `composer.write.returned`
- `composer.write.output_stat`

Include:

- `job_id`
- `output_path`
- `expected_duration`
- `output_size_bytes` after return

### B. Enable progress logging from MoviePy/ffmpeg

Do **not** keep `logger=None` while investigating.

Use a progress logger so operators can see:

- frame progress
- elapsed encode time
- whether encoding is still moving

### C. Capture failures with stderr

If render internally shells out to ffmpeg and fails, the investigation must capture:

- actual ffmpeg command
- return code
- stderr excerpt

Without this, the incident remains guesswork.

### D. Add a render timeout

`NORMALIZE` already has explicit subprocess timeouts.

`RENDER` needs a similar timeout or watchdog so a single stuck encode cannot block the worker indefinitely.

---

## Step 6: Separate Render Problems from Upload Problems

Once the render boundary is instrumented, classify the incident with this decision tree:

### Case A: `render.success` appears

Then check whether the next logs show:

- `stage.success` for `RENDER`
- `stage.start` for `UPLOAD_DROPBOX`
- `upload.start`

If yes, then and only then move the investigation to Dropbox.

### Case B: `render.failure` appears

Then the root cause is in render, not upload.

Collect:

- error type
- error message
- ffmpeg stderr
- clip/effect context

### Case C: no `render.success` and no `render.failure`

Then treat it as a hang/stall investigation.

This is currently the most likely class for this incident.

---

## Likely Root-Cause Candidates To Test

The investigation should explicitly test these possibilities:

### 1. Long but valid encode

The render may simply be slow because:

- the output is ~143 seconds
- there are multiple normalized source videos
- MoviePy is compositing several scenes
- final encode may be CPU-bound

### 2. `MoviePy` stall during `write_videofile()`

This is the strongest current suspicion because logs stop exactly at the boundary before `write_videofile()`.

### 3. ffmpeg encode issue not surfaced to logs

If `MoviePy` swallows or delays error surfacing, the worker may appear silent.

### 4. Resource saturation

Check for:

- CPU saturation
- memory pressure
- GPU/NVENC contention
- disk I/O bottleneck

### 5. Container/deployment interference

If deployment logic reacts to `unhealthy` and replaces the worker, the job may be interrupted mid-render.

This must be confirmed with lifecycle evidence, not assumed.

---

## Required Deliverables From The Investigation

The investigation is not complete until it produces:

1. A precise timeline for the affected job.
2. A classification of the failure mode:
   - slow render
   - stalled render
   - render exception
   - worker interruption
   - upload failure
3. The exact blocking call or exact exception.
4. The specific code path responsible.
5. A concrete fix proposal.
6. A regression test or runtime guard to prevent silent recurrence.

---

## Definition of Done

This incident can be considered understood only when:

- the team can explain why logs stopped after `composer.write`
- the team can prove whether `write_videofile()` was progressing, stuck, or failed
- the team can show why `UPLOAD_DROPBOX` never started, or prove that it did
- the system gains enough instrumentation that the next incident of this type can be diagnosed from logs alone

Until then, the current state should be described as:

> `maker8` completed pre-render stages successfully, entered `RENDER`, emitted `composer.write`, and then lost observability before any evidence of render completion or Dropbox upload.

