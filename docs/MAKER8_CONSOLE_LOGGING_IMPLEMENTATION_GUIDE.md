# Maker8 Console Logging Implementation Guide

## Goal

Mục tiêu là làm cho `maker8` in ra logs đơn giản, dễ đọc ngay trên console, nhưng vẫn đủ chi tiết để người vận hành biết:

1. job nào đang chạy
2. đang ở stage nào
3. stage nào fail
4. asset / scene / source nào gây fail
5. retry có xảy ra không
6. root cause cụ thể là gì

Guide này **không** bao gồm:

- Prometheus
- metrics endpoint
- dashboard
- HTTP health API

Chỉ tập trung vào **logs trên console**.

---

## Current Problem

Hiện tại `maker8` có log, nhưng chưa đủ để debug nhanh.

Ví dụ với lỗi:

```json
{
  "error": {
    "code": "RESOLVE_FAILED",
    "stage": "RESOLVE_ASSETS",
    "message": "Failed to resolve asset yt_5XGgp5Ltk7I: expected str, bytes or os.PathLike object, not NoneType"
  }
}
```

Người vận hành vẫn không biết ngay:

- asset đó có `source.kind` là gì
- `source.url` là gì
- `source.options.format` có bị `null` không
- connector nào đang chạy
- đang là attempt thứ mấy
- có retry tiếp không

Nghĩa là log hiện tại vẫn bắt operator phải đọc code.

---

## Desired Logging Outcome

Khi một job chạy, console phải hiện rõ luồng như sau:

```text
[INFO] job.start job_id=... correlation_id=...
[INFO] stage.start stage=VALIDATE attempt=1
[INFO] stage.success stage=VALIDATE duration_ms=12
[INFO] stage.start stage=RESOLVE_ASSETS attempt=1
[INFO] resolve.asset.start asset_id=yt_... source_kind=youtube url=https://...
[ERROR] resolve.asset.failure asset_id=yt_... source_kind=youtube format_spec=null error_type=ValueError error="Invalid yt-dlp format spec: None"
[WARN] stage.retry_scheduled stage=RESOLVE_ASSETS attempt=1 next_delay_sec=60
[INFO] stage.start stage=RESOLVE_ASSETS attempt=2
...
[ERROR] job.failed job_id=... stage=RESOLVE_ASSETS code=RESOLVE_FAILED retryable=true attempt=5
```

Chỉ nhìn console là đủ biết chuyện gì đang xảy ra.

---

## Implementation Principles

### 1. Keep logs human-readable in console

Không tối ưu cho machine parsing trước.
Ưu tiên:

- dễ đọc bằng mắt
- key context rõ ràng
- error message cụ thể
- stack trace có khi cần

### 2. Use stable event names

Dù log ở dạng console, vẫn cần event names ổn định để dễ grep:

- `job.start`
- `job.success`
- `job.failed`
- `stage.start`
- `stage.success`
- `stage.failure`
- `stage.retry_scheduled`
- `resolve.asset.start`
- `resolve.asset.success`
- `resolve.asset.failure`
- `subprocess.start`
- `subprocess.success`
- `subprocess.failure`

### 3. Always include core context

Mọi log liên quan tới job nên có:

- `job_id`
- `job_key` nếu đã có
- `correlation_id`

Mọi log liên quan tới stage nên có:

- `stage`
- `attempt`

Mọi log liên quan tới asset nên có:

- `asset_id`
- `source_kind`

Mọi log liên quan tới scene nên có:

- `scene_id`

---

## What To Change

## 1. Default to Console Logging

### File

- `src/maker8/config.py`
- `src/maker8/utils/logging.py`

### Required change

Giữ support cho `json` nếu cần, nhưng để operator debug dễ hơn thì local/staging nên default sang:

```python
log_format: str = "console"
```

Hoặc ít nhất đảm bảo env:

```env
MAKER8_LOG_FORMAT=console
MAKER8_LOG_LEVEL=DEBUG
```

### Required behavior

`ConsoleRenderer()` phải là format mặc định khi cần debug.

Không cần thêm framework log phức tạp hơn.

---

## 2. Add Job Lifecycle Logs

### File

- `src/maker8/pipeline/orchestrator.py`

### Add these events

#### `job.start`

Log khi bắt đầu xử lý request hợp lệ:

- `job_id`
- `correlation_id`
- `scene_count`
- `asset_count`

#### `job.success`

Log khi pipeline hoàn tất:

- `job_id`
- `job_key`
- `duration_ms`
- `output_video_path`
- `output_size_bytes`

#### `job.failed`

Log cuối cùng trước khi emit failed result / DLQ:

- `job_id`
- `job_key`
- `stage`
- `code`
- `retryable`
- `attempt`
- `error_type`
- `error_message`

Đây phải là log chính để operator grep.

---

## 3. Add Stage Lifecycle Logs

### File

- `src/maker8/pipeline/orchestrator.py`

### Required behavior

Trong `_execute_with_retry()`:

- log `stage.start` trước khi gọi `stage.execute(ctx)`
- log `stage.success` khi stage xong
- log `stage.failure` khi bắt `StageError`
- log `stage.retry_scheduled` khi có retry

### Fields

#### `stage.start`

- `job_id`
- `stage`
- `attempt`

#### `stage.success`

- `job_id`
- `stage`
- `attempt`
- `duration_ms`

#### `stage.failure`

- `job_id`
- `stage`
- `attempt`
- `error_code`
- `error_type`
- `error_message`
- `retryable`

#### `stage.retry_scheduled`

- `job_id`
- `stage`
- `attempt`
- `next_delay_sec`
- `max_attempts`
- `error_code`
- `error_message`

---

## 4. Improve Kafka Consumer Logs

### File

- `src/maker8/kafka/consumer.py`

### Required additions

Khi nhận message:

- log `consumer.message_received`
- include:
  - `topic`
  - `partition`
  - `offset`
  - `key`
  - `payload_size`

Khi handler bắt đầu:

- log `consumer.handler_started`
- include `job_id` nếu parse được

Khi handler xong:

- log `consumer.handler_finished`
- include `job_id`
- include `status=success|failed`

Khi commit:

- log `consumer.commit_succeeded`
- include topic/partition/offset

Khi commit fail:

- giữ `consumer.commit_failed`

Mục tiêu:

- operator có thể trace failed result ngược lại đúng Kafka message

---

## 5. Improve `RESOLVE_ASSETS` Logging

### File

- `src/maker8/pipeline/resolve.py`

### Required additions

Trước khi resolve từng asset:

- log `resolve.asset.start`
- include:
  - `job_id`
  - `asset_id`
  - `asset_type`
  - `source_kind`
  - sanitized `source_url`
  - `connector`

Khi resolve thành công:

- log `resolve.asset.success`
- include:
  - `asset_id`
  - `source_kind`
  - `filename`
  - `expected_type`

Khi resolve fail:

- log `resolve.asset.failure`
- include:
  - `asset_id`
  - `source_kind`
  - `connector`
  - `error_type`
  - `error_message`
  - source options quan trọng như:
    - `format_spec`
    - `max_duration_sec`

Mục tiêu:

- case như `expected str, bytes or os.PathLike object, not NoneType` phải nhìn log là biết ngay `format_spec=None`

---

## 6. Improve YouTube Connector Logs

### File

- `src/maker8/plugins/sources/youtube.py`

### Required additions

Trước khi build command:

- log `ytdlp.resolve.start`
- include:
  - `asset_id`
  - `url`
  - `format_spec`
  - `max_duration_sec`
  - `timeout_sec=120`

Trước khi gọi subprocess:

- log `subprocess.start`
- include:
  - `tool=yt-dlp`
  - `mode=resolve`
  - sanitized command

Khi subprocess success:

- log `subprocess.success`
- include:
  - `tool=yt-dlp`
  - `mode=resolve`
  - `returncode=0`
  - `duration_ms`

Khi subprocess fail:

- log `subprocess.failure`
- include:
  - `tool=yt-dlp`
  - `mode=resolve`
  - `returncode`
  - `stderr_excerpt`
  - `stdout_excerpt` nếu hữu ích
  - `duration_ms`

### Important validation

Trước khi gọi `subprocess.run`, validate:

- `url` không được empty
- `fmt` không được `None`

Nếu invalid:

- raise lỗi rõ ràng kiểu:
  - `Invalid yt-dlp format spec: None`
- log rõ trước khi fail

Không để Python ném generic `NoneType` process-arg error.

---

## 7. Improve HTTP Connector Logs

### File

- `src/maker8/plugins/sources/http_source.py`

### Required additions

Add logs:

- `http.download.start`
- `http.download.response`
- `http.download.success`
- `http.download.failure`

Include:

- `asset_id`
- `url`
- `status_code`
- `content_type`
- `content_length` nếu có
- `downloaded_bytes`
- `dest_path`

---

## 8. Improve Normalize / Render / Upload Logs

### Files

- `src/maker8/pipeline/normalize.py`
- `src/maker8/pipeline/render.py`
- `src/maker8/pipeline/upload.py`

### Normalize

Before ffmpeg:

- `subprocess.start tool=ffmpeg mode=normalize`

On failure:

- `subprocess.failure tool=ffmpeg mode=normalize`
- include stderr excerpt

### Render

At render start:

- `render.start`
- include scene count, asset count, output path

At render success:

- `render.success`
- include duration, output path, size

At render failure:

- `render.failure`
- include error type/message

### Upload

At upload start:

- `upload.start`
- include local path, remote path, file size

At upload failure:

- log specific Dropbox exception class
- include request id if available

---

## 9. Improve TTS Logs

### Files

- `src/maker8/pipeline/tts.py`
- `src/maker8/services/tts_client.py`

### Required additions

Log:

- `tts.scene.start`
- `tts.scene.success`
- `tts.scene.failure`

Include:

- `job_id`
- `scene_id`
- `provider`
- `lang`
- `preset_ref`
- `chars`
- `duration_ms`

If provider times out:

- log rõ `error_type=TimeoutError`
- include timeout seconds

If credential rotation is used:

- log key label or filename only
- không log full credential / API key

---

## 10. Add Final Failure Summary Log

### File

- `src/maker8/pipeline/orchestrator.py`

### Required event

- `job.failure_summary`

Log này phải gom tất cả context hữu ích nhất:

- `job_id`
- `job_key`
- `correlation_id`
- `stage`
- `code`
- `retryable`
- `attempt`
- `error_type`
- `error_message`
- `asset_id` nếu có
- `scene_id` nếu có
- `source_kind` nếu có

Đây là log mà operator dùng để kết luận incident.

---

## 11. Show Full Stack Trace Only When Useful

Không phải log nào cũng cần `log.exception`.

Rule:

- use `log.info` cho success path
- use `log.warning` cho retry path
- use `log.error` cho expected operational failures
- use `log.exception` cho unexpected exceptions hoặc nơi stack trace thực sự có giá trị

Avoid:

- spam stack trace lặp lại 5 lần cho cùng một retry nếu context log đã rõ

Nếu retry fail cùng một nguyên nhân nhiều lần:

- attempt đầu: có stack trace
- các attempt sau: có thể chỉ log concise summary

---

## 12. Keep Logs Safe

Never log:

- Kafka password
- Dropbox refresh token
- ElevenLabs API key
- Google credential contents

Allowed:

- credential filename
- `has_token=true/false`
- sanitized URL

If URL may contain secret query params:

- strip query string before logging

---

## Suggested Implementation Order

1. `utils/logging.py`
   - ensure console renderer is default/easy to enable
2. `pipeline/orchestrator.py`
   - add job/stage lifecycle logs
3. `kafka/consumer.py`
   - add message provenance logs
4. `pipeline/resolve.py`
   - add asset-level logs
5. `plugins/sources/youtube.py`
   - add command/context logs and explicit validation
6. `plugins/sources/http_source.py`
   - add richer HTTP logs
7. `pipeline/normalize.py`, `render.py`, `upload.py`, `tts.py`
   - add detailed stage logs

---

## Verification Checklist

Sau khi implement, operator phải nhìn console và trả lời được các case sau:

### Case 1: invalid YouTube format

Phải thấy rõ:

- `asset_id`
- `source_kind=youtube`
- `format_spec=None`
- fail ở `RESOLVE_ASSETS`
- có retry hay không

### Case 2: empty URL

Phải thấy rõ:

- asset nào thiếu URL
- connector nào fail

### Case 3: TTS timeout

Phải thấy rõ:

- scene nào fail
- provider nào fail
- timeout bao nhiêu giây

### Case 4: FFmpeg failure

Phải thấy rõ:

- input file nào
- output file nào
- stderr excerpt là gì

### Case 5: Dropbox upload failure

Phải thấy rõ:

- file nào đang upload
- remote path nào
- exception type nào
- retryable hay không

---

## Definition of Done

Implementation này hoàn tất khi:

- console log mặc định dễ đọc
- mỗi job có `start/success/failure`
- mỗi stage có `start/success/failure`
- retry được log rõ ràng
- asset/scene/source context hiện rõ trong lỗi
- connector/subprocess failures có đủ input context
- operator không cần đọc source code để hiểu phần lớn lỗi runtime

---

## Recommended First Deliverables

Nếu muốn làm nhanh nhưng hiệu quả nhất, hãy ưu tiên đúng 4 việc:

1. chuyển log format sang `console`
2. thêm `job.*` và `stage.*` lifecycle logs
3. thêm `resolve.asset.*` logs
4. sửa `youtube.py` để log rõ `format_spec` / command context trước khi fail

Chỉ 4 việc này thôi cũng đã tăng mạnh khả năng vận hành `maker8`.
