# Movie Maker – SPECS (v1)

## Overview

Pipeline:

```
Kafka nhận job JSON
→ ingest media (youtube_dlp + multi-site)
→ TTS theo narration (1 provider hệ thống)
→ dựng video theo scene
→ render (MoviePy/FFmpeg)
→ luôn upload Dropbox
→ external Publisher Worker download từ Dropbox
→ publish theo publish.targets[]
→ trả kết quả về Kafka
```

---

# 1) Kiến trúc tổng thể (đã chốt)

## 1.1 Services

### A) Render Worker

* Dockerized
* 1 instance = 1 workflow tại 1 thời điểm

Responsibilities:

* Validate + canonicalize RenderSpec
* Resolve / Download / Normalize assets (youtube_dlp / http)
* TTS narration (provider do hệ thống cấu hình)
* Apply effects plugins
* Render bằng MoviePy / FFmpeg
* Upload mp4 lên Dropbox
* Emit `video.render.result.v1`

---

### B) Publisher Worker

* Dockerized
* Scale độc lập

Responsibilities:

* Consume `video.render.result.v1`
* Download mp4 từ Dropbox (path + file_id + rev)
* Publish theo `publish.targets[]`
* Emit `video.publish.result.v1`

---

## 1.2 Kafka Topics

| Topic                     | Purpose         |
| ------------------------- | --------------- |
| `video.render.request.v1` | Job đầu vào     |
| `video.render.result.v1`  | Render handoff  |
| `video.publish.result.v1` | Publish outcome |
| `video.render.dlq.v1`     | DLQ render      |
| `video.publish.dlq.v1`    | DLQ publish     |

---

# 2) Contracts

## 2.1 Render Request – `video.render.request.v1`

> Không dùng `profile_version`.
> Provider/preset hệ thống lấy từ runtime config.

```json
{
  "job_id": "uuid",
  "spec_version": "1.0",
  "render_spec": { },
  "result": {
    "type": "kafka",
    "topic": "video.render.result.v1",
    "key": "{job_id}"
  },
  "trace": {
    "correlation_id": "..."
  }
}
```

---

## 2.2 Render Result – `video.render.result.v1`

> External publisher worker chỉ cần Dropbox path + file_id + rev.

```json
{
  "job_id": "uuid",
  "status": "DONE|FAILED",
  "job_key": "sha256:...",

  "dropbox": {
    "video": {
      "path": "/renders/<yyyy>/<mm>/<dd>/<job_id>.mp4",
      "file_id": "id:...",
      "rev": "...",
      "content_hash": "...",
      "sha256": "...",
      "bytes": 0,
      "mime": "video/mp4"
    },
    "manifest": {
      "path": "/renders/<yyyy>/<mm>/<dd>/<job_id>.manifest.json",
      "file_id": "id:...",
      "rev": "...",
      "sha256": "...",
      "bytes": 0,
      "mime": "application/json"
    }
  },

  "output_meta": {
    "duration": 0,
    "w": 0,
    "h": 0,
    "fps": 0,
    "size_bytes": 0
  },

  "publish_targets": [
    {
      "platform": "tiktok",
      "account_ref": "acct:tiktok:brandA",
      "metadata": {},
      "params": {}
    }
  ],

  "asset_report": [],
  "engine_versions": {
    "moviepy": "...",
    "ffmpeg": "...",
    "youtube_dlp": "..."
  },

  "error": {
    "code": "...",
    "stage": "...",
    "retryable": true,
    "message": "..."
  }
}
```

### Render Stage Enum

```
VALIDATE
RESOLVE_ASSETS
DOWNLOAD
NORMALIZE
TTS
RENDER
UPLOAD_DROPBOX
EMIT_RESULT
```

---

## 2.3 Publish Result – `video.publish.result.v1`

```json
{
  "job_id": "uuid",
  "job_key": "sha256:...",
  "status": "DONE|PARTIAL|FAILED",

  "dropbox_video": {
    "path": "/renders/.../<job_id>.mp4",
    "file_id": "id:...",
    "rev": "..."
  },

  "publish_report": [
    {
      "platform": "youtube",
      "account_ref": "acct:youtube:channel123",
      "status": "PUBLISHED|FAILED|PENDING",
      "post_id": "...",
      "error": null
    }
  ],

  "error": {
    "code": "...",
    "stage": "DOWNLOAD|PUBLISH|EMIT_RESULT",
    "retryable": true,
    "message": "..."
  }
}
```

---

# 3) RenderSpec (Scene-based, assets registry-only)

## 3.1 Root Schema

```json
{
  "spec_version": "1.0",
  "canvas": {
    "w": 1080,
    "h": 1920,
    "fps": 30,
    "bg": "#000000"
  },
  "defaults": {},
  "assets": [],
  "scenes": [],
  "output": {},
  "publish": {
    "targets": []
  }
}
```

---

## 3.2 Mandatory Rules

* Tất cả media external phải khai báo trong `assets[]`
* Scene/layer/audio chỉ dùng `asset_ref`
* Mỗi scene bắt buộc có `narration.text`

---

## 3.3 Defaults (Provider-agnostic TTS)

```json
{
  "defaults": {
    "narration": {
      "lang": "vi-VN",
      "tts_preset_ref": "tts:vi:default"
    },
    "scene_timing": {
      "head_pad_sec": 0.15,
      "tail_pad_sec": 0.45,
      "duration_mode": "auto_from_tts"
    }
  }
}
```

* `tts_preset_ref` resolve tại runtime từ TTS Preset Store
* Không chứa provider trong JSON

---

## 3.4 Assets Example

```json
{
  "id": "yt_main",
  "type": "video",
  "source": {
    "kind": "youtube",
    "url": "https://...",
    "options": {
      "format": "...",
      "max_duration_sec": 1800
    }
  }
}
```

---

## 3.5 Scene Schema

```json
{
  "scene_id": "s1",
  "duration": null,
  "narration": {
    "text": "..."
  },
  "layers": [],
  "audio_tracks": [],
  "effects": [],
  "transition_out": {}
}
```

### Duration Policy

```
duration = head_pad + tts_duration + tail_pad
```

---

# 4) Layer Layout Rules

## 4.1 Coordinate System

* Origin: top-left (0,0)
* Units: px
* Z-order: theo thứ tự mảng
* Safe-area hỗ trợ UI overlay avoidance

```json
"canvas": {
  "safe_area": {
    "top": 120,
    "right": 80,
    "bottom": 260,
    "left": 80
  }
}
```

---

## 4.2 Base Layer

```json
{
  "layer_id": "...",
  "type": "image|video|text",
  "rect": { "x": 0, "y": 0, "w": 1080, "h": 1920 },
  "anchor": "top_left|center|bottom_left|bottom_right",
  "opacity": 1.0,
  "rotation_deg": 0,
  "scale": 1.0
}
```

---

## 4.3 Image / Video Layer

```json
{
  "layer_id": "main_video",
  "type": "video",
  "asset_ref": "yt_main",
  "rect": { "x": 0, "y": 0, "w": 1080, "h": 1920 },
  "fit": "cover|contain",
  "align": "center|top|bottom|left|right",
  "trim": { "in": 5.0, "out": 15.0 }
}
```

---

## 4.4 Text Layer

```json
{
  "layer_id": "caption",
  "type": "text",
  "text": "...",
  "rect": { "x": 80, "y": 1450, "w": 920, "h": 360 },
  "text_align": "left|center|right",
  "valign": "top|center|bottom",
  "style": {
    "font_ref": "font:inter:bold",
    "size": 56,
    "color": "#FFFFFF",
    "stroke_color": "#000000",
    "stroke_width": 3,
    "line_height": 1.15,
    "wrap": true
  }
}
```

---

# 5) Canonicalization & Job Key

## 5.1 maker8(render_spec)

Canonicalization rules:

1. UTF-8 serialize
2. Sort object keys lexicographically
3. Sort `assets[]` by `id`
4. Sort `publish.targets[]` by `(platform, account_ref)`
5. Keep order of `scenes[]`
6. Keep order of `layers[]`
7. Normalize floats to 6 decimals
8. Normalize newline `\r\n` → `\n`

---

## 5.2 Job Key

```
job_key = SHA256(maker8(render_spec))
```

---

# 6) Dropbox Output

## 6.1 Path Convention

```
/renders/<yyyy>/<mm>/<dd>/<job_id>.mp4
/renders/<yyyy>/<mm>/<dd>/<job_id>.manifest.json
```

---

## 6.2 Manifest (Recommended)

```json
{
  "job_id": "uuid",
  "job_key": "sha256:...",
  "dropbox": {
    "video": {
      "path": "...",
      "file_id": "id:...",
      "rev": "...",
      "sha256": "...",
      "bytes": 0
    }
  },
  "output_meta": {
    "duration": 0,
    "w": 0,
    "h": 0,
    "fps": 0
  },
  "publish_targets": [
    { "platform": "...", "account_ref": "..." }
  ],
  "engine_versions": {
    "moviepy": "...",
    "ffmpeg": "...",
    "youtube_dlp": "..."
  }
}
```

---

# 7) Retry / DLQ

## 7.1 Render Retry

* max_attempts: 5
* exponential backoff: 1m → 6h
* retryable stages:

  * RESOLVE_ASSETS
  * DOWNLOAD
  * TTS
  * UPLOAD_DROPBOX
  * EMIT_RESULT

---

## 7.2 Publish Retry

* max_attempts: 8
* exponential backoff: 5m → 12h
* retryable:

  * DOWNLOAD
  * PUBLISH
  * EMIT_RESULT

---

## 7.3 DLQ Payload

```json
{
  "job_id": "uuid",
  "job_key": "sha256:...",
  "failed_stage": "...",
  "attempts": 5,
  "last_error": {
    "code": "...",
    "message": "..."
  },
  "dropbox": {
    "video_path": "..."
  },
  "trace": {
    "correlation_id": "..."
  }
}
```

---

# 8) Plugins

## 8.1 Effects Plugins

```
manifest(id, version, priority, cost, deterministic)
schema()
apply(ctx, ir, instance) -> ir
```

Rules:

* No network
* No spawn process
* Enforce budgets

---

## 8.2 SourceConnector Plugins

```
manifest()
schema()
resolve() -> ResolvedAssetPlan
```

* youtube_dlp for YouTube / multi-site
* Deterministic format selection

---

## 8.3 Publisher Plugins

```
publish(ctx, dropbox_video_ref, target) -> PublishResult
```

Supported:

* publish/youtube
* publish/tiktok
* publish/facebook

---

# END OF SPEC (v1)