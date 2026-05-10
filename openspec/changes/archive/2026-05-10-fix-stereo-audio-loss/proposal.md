## Why

`NormalizeStage._normalize_audio` hard-codes `-ac 1` (mono, 44.1 kHz WAV) for every audio asset unconditionally. Any stereo or surround music track, sound effect, or background audio submitted by the caller is silently downmixed to mono before the composer ever sees it, degrading audio quality on all jobs that include stereo audio.

## What Changes

- Remove the unconditional `-ac 1` flag from `_normalize_audio` in `src/maker8/pipeline/normalize.py`.
- Probe the source audio channel count with `ffprobe` and preserve it in the normalised WAV (up to a configurable max, defaulting to 2 for stereo).
- Keep the 44.1 kHz sample-rate normalisation unchanged — only channel count changes.
- Log the detected channel count and whether channels were preserved or clamped.

## Capabilities

### New Capabilities

- `stereo-audio-preservation`: The normalise stage SHALL preserve the source audio channel count (up to a configurable maximum, default 2) instead of forcing mono. Audio tracks submitted as stereo will produce stereo normalised WAVs; mono sources remain mono.

### Modified Capabilities

<!-- none -->

## Impact

- `src/maker8/pipeline/normalize.py` — `_normalize_audio` method; new `_probe_audio_channels` helper
- `src/maker8/config.py` — optional `normalize_max_audio_channels` setting (default 2)
- `docs/schemas/` — no wire model changes; no schema regeneration needed
- `tests/` — new unit tests for `_probe_audio_channels` and the preserved-channel normalise path
