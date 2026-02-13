# TTS Credential Rotation (Round-Robin)

Maker8 supports **multiple API keys / service accounts** for both
Google Cloud TTS and ElevenLabs.  Keys are rotated per-video in a
deterministic round-robin cycle.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ TTSService (façade)                                         │
│                                                             │
│  ┌──────────────────┐       ┌──────────────────┐            │
│  │ KeyRing[Path]     │       │ KeyRing[str]      │           │
│  │ (Google Cloud)    │       │ (ElevenLabs)      │           │
│  │                   │       │                   │           │
│  │  key-a.json ──►   │       │  key-a.txt ──►    │           │
│  │  key-b.json       │       │  key-b.txt        │           │
│  │  key-c.json       │       │  key-c.key        │           │
│  └──────────────────┘       └──────────────────┘            │
│                                                             │
│  ┌─────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ GTTSProvider │  │ GoogleCloudTTS   │  │ ElevenLabs      │  │
│  │ (free)       │  │ Provider         │  │ Provider        │  │
│  └─────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                          │
               TTSStage.execute(ctx)
                 1. next_google_credentials()   ← advances ring
                 2. next_elevenlabs_key()        ← advances ring
                 3. for scene in scenes:
                      synthesize(…, creds=…)     ← same key
```

### Key rotation granularity

* **Per-video** – the pipeline calls `next_*()` once at the start of
  each `TTSStage.execute()`.
* All scenes within the same video share a single credential.
* The next video rotates to the next key.

This avoids mid-video credential switches which would complicate error
handling and quota tracking.

---

## Setup

### Google Cloud TTS

1. Create service accounts and download JSON keys.
2. Place them in `gg-tts-keys/`:

   ```
   gg-tts-keys/
   ├── account-a.json
   ├── account-b.json
   └── account-c.json
   ```

3. Set `MAKER8_GOOGLE_TTS_KEYS_DIR=gg-tts-keys` in `.env`
   (already the default).

**Fallback:** If the directory is empty or missing, the provider uses
Application Default Credentials (ADC) or
`MAKER8_GOOGLE_APPLICATION_CREDENTIALS`.

### ElevenLabs

1. Generate API keys from the ElevenLabs dashboard.
2. Save each key in a separate `.txt` or `.key` file:

   ```
   elevenlabs-keys/
   ├── team-a.txt       # contains: sk_abc123…
   └── team-b.key       # contains: sk_def456…
   ```

3. Set `MAKER8_ELEVENLABS_KEYS_DIR=elevenlabs-keys` in `.env`
   (already the default).

**Fallback:** If the directory is empty or missing, the provider uses
`MAKER8_ELEVENLABS_API_KEY`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `MAKER8_TTS_PROVIDER` | `gtts` | Default provider when preset omits it |
| `MAKER8_TTS_PRESETS_PATH` | `config/tts_presets.json` | Preset definitions |
| `MAKER8_GOOGLE_CLOUD_TTS_ENABLED` | `false` | Enable Google Cloud TTS |
| `MAKER8_GOOGLE_APPLICATION_CREDENTIALS` | *(empty)* | Single-key fallback |
| `MAKER8_GOOGLE_TTS_KEYS_DIR` | `gg-tts-keys` | Multi-key directory |
| `MAKER8_ELEVENLABS_API_KEY` | *(empty)* | Single-key fallback |
| `MAKER8_ELEVENLABS_KEYS_DIR` | `elevenlabs-keys` | Multi-key directory |

---

## Startup log

When the worker starts, the TTS service logs how many keys were loaded:

```
tts_service.ready  default_provider=gtts  google_keys=15  elevenlabs_keys=0
```

If a directory is missing, a warning is logged and the single-key
fallback is used automatically:

```
tts.google_key_ring_unavailable  directory=gg-tts-keys  hint="Place service-account JSON files …"
```

---

## File layout

```
src/maker8/
├── services/
│   ├── key_ring.py        # Generic KeyRing[T] – thread-safe round-robin
│   └── tts_client.py      # Providers + TTSService (façade)
├── pipeline/
│   └── tts.py             # TTSStage – acquires keys per video
└── config.py              # Settings: key directory paths
```

---

## Adding a new TTS provider

1. Subclass `TTSProvider` in `services/tts_client.py`.
2. Register it in `TTSService._PROVIDERS`.
3. If the provider needs key rotation, add a `KeyRing` field and
   a `next_*()` method to `TTSService`.
4. Add preset entries to `config/tts_presets.json`.
5. Update the TTS stage to call the new `next_*()` method.
