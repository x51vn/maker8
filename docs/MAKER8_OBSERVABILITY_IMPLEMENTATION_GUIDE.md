# Maker8 Observability Implementation Guide

## Goal

Triển khai observability cho `maker8` để người vận hành có thể trả lời nhanh 5 câu hỏi:

1. Worker còn sống không?
2. Worker có thực sự sẵn sàng xử lý job không?
3. Worker đang xử lý job nào, ở stage nào, attempt thứ mấy?
4. Job fail vì nguyên nhân gì, thuộc nhóm lỗi nào, có retry hay không?
5. Tình trạng fail hiện tại là lỗi cục bộ một job hay lỗi hệ thống đang lan rộng?

Guide này tập trung vào implementation thực dụng, phù hợp với `maker8` hiện tại:

- 1 worker process đồng bộ
- không có API server riêng
- đang dùng `structlog`
- push kết quả qua Kafka

---

## Scope

Observability cần có 4 lớp:

1. Structured logs
2. Runtime state + health/readiness
3. Metrics cho dashboard/alert
4. Failure surfacing trong result/DLQ

Không coi một lớp là đủ thay cho các lớp còn lại.

Ví dụ:

- log tốt nhưng không có metric: khó biết fail rate tăng đột biến
- metric tốt nhưng không có runtime state: khó biết worker đang kẹt ở đâu
- result/DLQ có error code nhưng không có log chi tiết: khó tìm root cause

---

## Current Gaps

Các blind spots chính của `maker8` hiện tại:

- health chỉ là file `/tmp/maker8_healthy`, phản ánh liveness rất yếu
- không có readiness riêng cho Kafka / Dropbox / TTS / plugin registry
- không có current worker state cho operator xem worker đang làm gì
- không có metrics endpoint
- retry có log nhưng không có metric/alert tương ứng
- FAILED result và DLQ còn quá nghèo context
- không có phân loại lỗi rõ ràng theo stage/code/dependency

---

## Recommended Architecture

### 1. Add an `observability` package

Tạo package mới:

```text
src/maker8/observability/
  __init__.py
  state.py
  metrics.py
  health.py
  helpers.py
```

Vai trò:

- `state.py`: giữ worker runtime state trong memory và flush ra file JSON
- `metrics.py`: định nghĩa Prometheus metrics và helper APIs
- `health.py`: tổng hợp liveness/readiness/status snapshot
- `helpers.py`: tiện ích đo thời gian, sanitize payload, truncate stderr/stdout

### 2. Do not add a full web framework

Không cần kéo cả FastAPI/Starlette vào `maker8`.

Hướng triển khai gọn nhất:

- dùng `prometheus_client` để mở metrics HTTP port riêng
- expose state/health dưới dạng JSON file trong `/tmp` hoặc `MAKER8_WORK_DIR`

Nếu sau này cần HTTP `/healthz` / `/readyz`, hãy dùng một server nhỏ riêng hoặc cân nhắc thêm một lightweight endpoint layer sau.

### 3. Treat observability as first-class runtime state

Không nên chỉ log tự phát ở từng file.

Cần một nơi tập trung quản lý:

- current job
- current stage
- current attempt
- last success
- last failure
- dependency readiness
- retry sleep until

---

## Implementation Plan

## Phase 1: Structured Logging Baseline

Triển khai theo tài liệu hiện có:

- [MAKER8_LOGGING_IMPROVEMENT_INSTRUCTIONS.md](/home/<user>/IdeaProjects/maker8/docs/MAKER8_LOGGING_IMPROVEMENT_INSTRUCTIONS.md)

Mục tiêu phase này:

- mọi stage có `start/success/failure`
- mọi retry có log rõ ràng
- mọi connector/subprocess failure có đủ context
- mọi job log có `job_id`, `job_key`, `correlation_id`

Files cần sửa:

- `src/maker8/kafka/consumer.py`
- `src/maker8/pipeline/orchestrator.py`
- `src/maker8/pipeline/resolve.py`
- `src/maker8/pipeline/download.py`
- `src/maker8/pipeline/normalize.py`
- `src/maker8/pipeline/tts.py`
- `src/maker8/pipeline/render.py`
- `src/maker8/pipeline/upload.py`
- `src/maker8/pipeline/emit.py`
- `src/maker8/plugins/sources/youtube.py`
- `src/maker8/plugins/sources/http_source.py`
- `src/maker8/utils/logging.py`

Definition of done:

- root cause phổ biến như `RESOLVE_FAILED`, `TTS_FAILED`, `UPLOAD_FAILED` có thể chẩn đoán chỉ từ logs

---

## Phase 2: Runtime State and Health

### 2.1 Add `WorkerState`

Tạo một state object trung tâm, ví dụ:

```python
@dataclass
class WorkerState:
    process_started_at: float
    consumer_running: bool
    current_job_id: str | None
    current_job_key: str | None
    current_stage: str | None
    current_attempt: int | None
    current_asset_id: str | None
    current_scene_id: str | None
    stage_started_at: float | None
    retry_sleep_until: float | None
    last_success_at: float | None
    last_failure_at: float | None
    last_failure_code: str | None
    last_failure_stage: str | None
    last_failure_job_id: str | None
    last_kafka_partition: int | None
    last_kafka_offset: int | None
```

State này phải được update tại các điểm:

- app startup/shutdown
- khi consumer nhận message
- khi orchestrator bắt đầu job
- khi vào mỗi stage
- khi schedule retry
- khi job success/fail

### 2.2 Flush state ra file JSON

Viết state snapshot ra file, ví dụ:

- `/tmp/maker8_status.json`

Hoặc:

- `${MAKER8_WORK_DIR}/maker8_status.json`

File này phục vụ:

- docker healthcheck nâng cao
- operator đọc nhanh
- sidecar collector nếu cần

Suggested JSON shape:

```json
{
  "service": "maker8",
  "version": "0.1.0",
  "started_at": "...",
  "consumer_running": true,
  "current_job": {
    "job_id": "...",
    "job_key": "...",
    "stage": "RESOLVE_ASSETS",
    "attempt": 2,
    "asset_id": "yt_123",
    "scene_id": null,
    "stage_started_at": "...",
    "elapsed_sec": 14.2
  },
  "retry": {
    "sleep_until": "...",
    "delay_sec": 60.0
  },
  "last_success": {
    "job_id": "...",
    "at": "..."
  },
  "last_failure": {
    "job_id": "...",
    "stage": "RESOLVE_ASSETS",
    "code": "RESOLVE_FAILED",
    "at": "..."
  }
}
```

### 2.3 Define 3 health semantics clearly

Phải tách:

- `liveness`
- `readiness`
- `degraded`

Suggested meaning:

- `liveness`: process còn chạy, event loop/consumer loop chưa tắt bất thường
- `readiness`: Kafka producer/consumer init xong, plugin registry load xong, work dir writable
- `degraded`: process còn sống nhưng dependency/config/runtime state có vấn đề

Examples of `degraded`:

- Dropbox auth validation fail
- không load được key ring kỳ vọng
- worker đang retry kéo dài
- quá lâu không có successful job dù vẫn nhận traffic

### 2.4 Health files

Không chỉ giữ một file `/tmp/maker8_healthy`.

Recommended:

- `/tmp/maker8_live`
- `/tmp/maker8_ready`
- `/tmp/maker8_status.json`

Logic:

- `live`: tạo khi process start, xóa khi shutdown
- `ready`: chỉ tạo khi bootstrap dependencies thành công
- `status.json`: cập nhật liên tục theo runtime state

---

## Phase 3: Metrics

### 3.1 Add Prometheus metrics

Thêm dependency:

- `prometheus-client`

Expose metrics qua một port cấu hình được, ví dụ:

- `MAKER8_METRICS_ENABLED=true`
- `MAKER8_METRICS_PORT=9108`

Sử dụng:

```python
from prometheus_client import start_http_server
```

Không cần web framework.

### 3.2 Metrics to implement

#### Counters

- `maker8_jobs_received_total`
- `maker8_jobs_succeeded_total`
- `maker8_jobs_failed_total`
- `maker8_invalid_payload_total`
- `maker8_dlq_emitted_total`
- `maker8_result_emitted_total`
- `maker8_retries_scheduled_total`
- `maker8_subprocess_failures_total`
- `maker8_dependency_failures_total`

Recommended labels:

- `stage`
- `error_code`
- `source_kind`
- `dependency`

#### Histograms

- `maker8_job_duration_seconds`
- `maker8_stage_duration_seconds`
- `maker8_subprocess_duration_seconds`
- `maker8_tts_duration_seconds`
- `maker8_download_bytes`

Recommended labels:

- `stage`
- `status`
- `source_kind`
- `provider`

#### Gauges

- `maker8_worker_up`
- `maker8_worker_ready`
- `maker8_job_in_progress`
- `maker8_current_stage`
- `maker8_retry_sleep_seconds`
- `maker8_last_success_unixtime`
- `maker8_last_failure_unixtime`
- `maker8_kafka_consumer_running`

Lưu ý:

- `current_stage` có thể khó biểu diễn bằng label ổn định trong Prometheus
- thực dụng hơn là giữ gauge `job_in_progress=1/0` và để `current stage` nằm ở `status.json`

### 3.3 Error cardinality control

Không được dùng raw exception message làm metric label.

Allowed labels:

- `stage`
- `error_code`
- `source_kind`
- `provider`

Không dùng:

- full URL
- asset_id
- job_id
- raw exception string

Các giá trị đó nên ở logs hoặc status snapshot, không phải metric labels.

---

## Phase 4: Result and DLQ Enrichment

Hiện tại FAILED result và DLQ còn thiếu thông tin cho operator.

### 4.1 Enrich `FAILED` RenderResult

Giữ backward compatibility nếu có thể, nhưng bổ sung thêm context vận hành ở các field cho phép.

Ít nhất cần có trong failure summary log, và nếu contract cho phép thì trong payload:

- `attempt`
- `max_attempts`
- `failed_entity_type`
- `failed_entity_id`
- `source_kind`
- `scene_id`
- `partial_asset_report`

Nếu không muốn sửa wire contract ngay:

- log đầy đủ
- và thêm snapshot tương ứng vào DLQ trước

### 4.2 Enrich DLQ payload

DLQ là nơi nên có forensic context hơn `FAILED result`.

Recommended additions:

- sanitized original input excerpt
- current stage
- attempt/max_attempts
- failed asset/scene
- source kind
- connector/plugin class
- kafka topic/partition/offset
- dependency summary

Nếu thay đổi contract khó, hãy version hóa hoặc thêm object `debug_context`.

---

## Phase 5: Operator-Facing Signals

### 5.1 Minimum operator surfaces

Khi xong observability baseline, operator phải có thể xem:

- live/ready status
- current job snapshot
- recent failure trend
- top errors by stage/code
- retrying jobs count
- last successful completion

### 5.2 Dashboard recommendations

Dashboard tối thiểu nên có:

1. Worker Overview
   - worker up
   - ready
   - job in progress
   - current stage
   - time since last success

2. Throughput
   - jobs received/min
   - jobs succeeded/min
   - jobs failed/min

3. Failure Breakdown
   - failures by stage
   - failures by error_code
   - failures by source_kind

4. Retry Behavior
   - retries scheduled/min
   - jobs currently sleeping in retry
   - longest retry wait

5. Stage Latency
   - p50/p95 stage duration
   - p50/p95 full job duration

6. Dependency Issues
   - Dropbox auth failures
   - TTS provider failures
   - yt-dlp/ffmpeg subprocess failures

### 5.3 Alerts

Suggested alerts:

- worker down
- worker not ready > N minutes
- no successful jobs for N minutes while jobs are being received
- failure rate exceeds threshold
- repeated same `error_code` spike
- retry backlog too high
- stage duration exceeds threshold

---

## Concrete File-Level Work

### Add new files

- `src/maker8/observability/state.py`
- `src/maker8/observability/metrics.py`
- `src/maker8/observability/health.py`
- `src/maker8/observability/helpers.py`

### Update existing files

- `src/maker8/app.py`
  - start metrics server
  - initialize liveness/readiness/state files
  - update worker lifecycle state

- `src/maker8/kafka/consumer.py`
  - capture topic/partition/offset/key
  - update worker state on message receive
  - metric counters for received/commit failure/invalid payload

- `src/maker8/pipeline/orchestrator.py`
  - central place for stage lifecycle instrumentation
  - update current stage/attempt/retry state
  - emit final success/failure summaries

- `src/maker8/pipeline/*`
  - stage-specific metrics and more precise failure context

- `src/maker8/plugins/sources/youtube.py`
  - log/signal invalid `format_spec`
  - expose subprocess failures cleanly

- `src/maker8/plugins/sources/http_source.py`
  - log response metadata and byte counters

- `src/maker8/services/tts_client.py`
  - metrics per provider
  - timeout/failure counters

- `src/maker8/services/dropbox_client.py`
  - upload latency metrics
  - auth/server/rate-limit counters

---

## Data Hygiene Rules

Observability phải hữu ích nhưng an toàn.

Never log or export:

- Kafka password
- Dropbox tokens
- ElevenLabs API keys
- Google credential file contents

Allowed:

- key filename
- credential presence booleans
- sanitized URLs
- truncated stderr/stdout

For payload snapshots:

- redact secrets
- truncate large fields
- avoid dumping full binary-heavy payloads

---

## Testing Plan

### Unit tests

Thêm test cho:

- worker state transitions
- health snapshot generation
- metrics counters/gauges/histograms update đúng
- sanitize/truncate helpers

### Integration-style tests

Simulate các case:

1. successful YouTube resolve/download
2. invalid YouTube `format = null`
3. invalid payload parse failure
4. TTS timeout
5. FFmpeg failure
6. Dropbox auth failure
7. retryable failure then success
8. retryable failure exhausted

Assertions phải bao gồm:

- log event xuất hiện đúng
- metrics tăng đúng
- status snapshot cập nhật đúng
- FAILED result / DLQ có context đúng

### Manual operator verification

Sau khi deploy staging:

1. submit một job success
2. submit một job fail có kiểm soát
3. xác nhận operator có thể từ dashboard/log/status file trả lời:
   - job nào fail
   - stage nào fail
   - dependency/input nào gây fail
   - có retry không
   - worker hiện có bị stuck không

---

## Rollout Strategy

### Step 1

Ship structured logging + worker state JSON trước.

Lý do:

- ít rủi ro nhất
- tăng khả năng chẩn đoán ngay

### Step 2

Ship Prometheus metrics.

Lý do:

- cho dashboard/alerting
- không thay đổi business logic

### Step 3

Enrich FAILED/DLQ payloads.

Lý do:

- có thể động chạm contract
- nên làm sau khi đã có logs/metrics/state ổn định

### Step 4

Thêm alerting/dashboard chính thức.

---

## Definition of Done

`maker8` observability được coi là đủ dùng khi:

- operator biết worker `live`, `ready`, `degraded` hay không
- operator biết worker đang xử lý job nào và ở stage nào
- operator biết top failure codes theo stage trong khoảng thời gian gần đây
- retry có thể theo dõi được bằng cả log và metric
- một FAILED job có thể được chẩn đoán mà không phải đọc source code
- dependency issues như Kafka/TTS/Dropbox có signal riêng
- staging có dashboard/alerts cơ bản hoạt động

---

## Recommended First Deliverables

Nếu chỉ chọn 5 thứ làm trước, hãy làm đúng thứ tự này:

1. stage lifecycle logs + failure summary logs
2. `maker8_status.json` với current job/stage/attempt
3. liveness/readiness/degraded semantics rõ ràng
4. Prometheus counters/histograms/gauges cơ bản
5. DLQ enrichment cho forensic debugging

Đó là baseline observability hợp lý nhất cho `maker8` hiện tại.
