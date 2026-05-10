## ADDED Requirements

### Requirement: Normalise stage preserves source audio channel count
The normalise stage SHALL probe the source audio file's channel count using `ffprobe` before normalising. The normalised WAV SHALL use the probed channel count, clamped to `normalize_max_audio_channels` (default 2). A stereo source SHALL produce a stereo normalised WAV; a mono source SHALL remain mono.

#### Scenario: Stereo source produces stereo normalised WAV
- **WHEN** the source audio file has 2 channels
- **THEN** the normalised WAV is written with `-ac 2` and contains 2 audio channels

#### Scenario: Mono source remains mono
- **WHEN** the source audio file has 1 channel
- **THEN** the normalised WAV is written with `-ac 1` and contains 1 audio channel

#### Scenario: Surround source is clamped to max channels
- **WHEN** the source audio file has 6 channels and `normalize_max_audio_channels` is 2
- **THEN** the normalised WAV is written with `-ac 2` (clamped) and a log entry notes the clamping

#### Scenario: Sample rate is always 44100 Hz regardless of channel count
- **WHEN** any audio source is normalised
- **THEN** the normalised WAV always uses `-ar 44100`

---

### Requirement: Probe failure falls back to mono
When `ffprobe` cannot determine the channel count (timeout, corrupt file, unexpected output format), the normalise stage SHALL fall back to `-ac 1` (mono) and log a warning. The job SHALL NOT fail due to a probe failure.

#### Scenario: ffprobe timeout falls back to mono
- **WHEN** the `ffprobe` channel probe times out
- **THEN** the normalised WAV is written with `-ac 1` and a `normalize.audio_probe_failed` warning is logged

#### Scenario: ffprobe returns unexpected output falls back to mono
- **WHEN** `ffprobe` returns non-numeric output for the channel count
- **THEN** the normalised WAV is written with `-ac 1` and a `normalize.audio_probe_failed` warning is logged

---

### Requirement: Channel count config is operator-controlled
An operator SHALL be able to set `normalize_max_audio_channels` in `config.py` (env var: `MAKER8_NORMALIZE_MAX_AUDIO_CHANNELS`) to control the maximum channel count preserved during normalisation. The default SHALL be 2 (stereo). Setting it to 1 SHALL restore the previous mono-only behaviour.

#### Scenario: Default max channels is 2
- **WHEN** `MAKER8_NORMALIZE_MAX_AUDIO_CHANNELS` is not set
- **THEN** stereo sources are preserved as stereo (channels ≤ 2 pass through unchanged)

#### Scenario: Operator sets max to 1 to force mono
- **WHEN** `MAKER8_NORMALIZE_MAX_AUDIO_CHANNELS=1`
- **THEN** all audio is normalised to mono regardless of source channel count
