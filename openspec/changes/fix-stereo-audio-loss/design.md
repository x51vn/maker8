## Context

`NormalizeStage._normalize_audio` (normalize.py line 784) converts every audio asset to a WAV with `-ac 1` (mono) and `-ar 44100`. The mono downmix was introduced for "consistent MoviePy handling" but is no longer necessary: MoviePy 2.x and FFmpeg both handle multi-channel WAV correctly. The fix is minimal: probe the source channel count with `ffprobe`, clamp to a configurable max (default 2), and pass the clamped value as `-ac N` to the normalise command. No wire model, schema, or composer changes are needed.

## Goals / Non-Goals

**Goals:**
- Probe source audio channel count before normalising
- Preserve channels up to `normalize_max_audio_channels` (default 2)
- Keep 44.1 kHz sample-rate normalisation unchanged
- Log detected channels and whether clamping occurred
- Add `normalize_max_audio_channels` to `config.py` with default 2

**Non-Goals:**
- Changing how the composer mixes audio tracks together
- Supporting surround (> 2 channel) output in the final render
- Changing the output container format (still WAV)
- Any wire model or schema changes

## Decisions

**Decision: Probe with `ffprobe`, clamp to `normalize_max_audio_channels`, default 2**

`ffprobe` is already imported and used in normalize.py for video stream detection. Re-using it for audio channel probing adds no new dependency. Defaulting the cap to 2 (stereo) preserves existing behaviour for any surround source that might appear unexpectedly, while fixing the mono regression for the common stereo case.

Alternatives considered:
- Read channels from MoviePy `AudioFileClip.nchannels`. Rejected: requires opening the file with MoviePy before normalising, adds overhead and an extra dependency inside the normalise stage.
- Always pass `-ac 2`. Rejected: would upscale mono sources to stereo unnecessarily; probing and clamping is more correct.

**Decision: Fall back to `-ac 1` on probe failure**

If `ffprobe` fails to determine channel count (timeout, corrupt file, unexpected output), the existing mono behaviour is preserved. This is the safest fallback — a degraded stereo asset is preferable to a failed job.

**Decision: Config setting in `config.py`, not in the wire model**

Channel count is an operator concern (server capability, MoviePy/FFmpeg compatibility), not a per-job caller concern. Keeping it in config avoids wire model churn and keeps `AudioTrack` unchanged.

## Risks / Trade-offs

[Risk] A normalised WAV cached from before this change is stereo-mono but the new code would regenerate it stereo. The existing cache-hit guard (`_is_valid_media`) checks size and ffprobe readability — a previously-normalised mono WAV would still pass that guard and be reused as mono.
Mitigation: This is acceptable during transition; the cache path already carries a `normalize.reuse_existing` log line so operators can identify stale cache entries. Production deploys should clear normalise output dirs when upgrading.

[Risk] MoviePy 2.x stereo WAV handling is untested in the existing test suite.
Mitigation: New unit tests mock `ffprobe` output and assert the correct `-ac N` flag is passed; no real audio files needed.

## Migration Plan

No wire format changes. Existing cached mono WAVs will be reused until evicted or cache dir cleared. No action required on upgrade unless operators want to re-normalise existing cached assets.
