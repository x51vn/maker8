## 1. Config

- [x] 1.1 Read `src/maker8/config.py` to confirm current config field patterns
- [x] 1.2 Add `normalize_max_audio_channels: int = 2` to `Settings` in `config.py`, sourced from env var `MAKER8_NORMALIZE_MAX_AUDIO_CHANNELS`

## 2. Audio Channel Probe Helper

- [x] 2.1 Read `src/maker8/pipeline/normalize.py` lines 97–122 to confirm `_has_video_stream` pattern for ffprobe usage
- [x] 2.2 Add `_probe_audio_channels(path: Path, timeout: int = 10) -> int` function in `normalize.py` that:
  - runs `ffprobe -v error -select_streams a:0 -show_entries stream=channels -of csv=p=0 <path>`
  - parses stdout as an integer and returns it
  - returns `1` (mono fallback) and logs `normalize.audio_probe_failed` on any exception or parse error

## 3. Update `_normalize_audio`

- [x] 3.1 Read `src/maker8/pipeline/normalize.py` lines 783–830 to confirm current `_normalize_audio` signature and FFmpeg command
- [x] 3.2 In `_normalize_audio`, call `_probe_audio_channels(src)` to get `detected_channels`
- [x] 3.3 Compute `target_channels = min(detected_channels, settings.normalize_max_audio_channels)` and replace the hard-coded `-ac 1` with `-ac {target_channels}`
- [x] 3.4 Log `normalize.audio_channels` with `detected=detected_channels`, `target=target_channels`, `clamped=(detected_channels != target_channels)`

## 4. Tests

- [x] 4.1 Create `tests/test_stereo_audio_preservation.py` with unit tests covering:
  - (a) `_probe_audio_channels` with mocked ffprobe returning `"2\n"` → returns `2`
  - (b) `_probe_audio_channels` with mocked ffprobe returning `"1\n"` → returns `1`
  - (c) `_probe_audio_channels` with mocked ffprobe raising `subprocess.TimeoutExpired` → returns `1`
  - (d) `_probe_audio_channels` with mocked ffprobe returning non-numeric output → returns `1`
  - (e) `_normalize_audio` with a stereo source and default max (2) → FFmpeg called with `-ac 2`
  - (f) `_normalize_audio` with a stereo source and `normalize_max_audio_channels=1` → FFmpeg called with `-ac 1`
  - (g) `_normalize_audio` with a 6-channel source and default max (2) → FFmpeg called with `-ac 2`
- [x] 4.2 Run `python -m pytest tests/test_stereo_audio_preservation.py -v` and confirm all tests pass
- [x] 4.3 Run `python -m pytest tests/` to confirm no regressions
