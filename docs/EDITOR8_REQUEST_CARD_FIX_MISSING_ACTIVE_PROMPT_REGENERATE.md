# Request Card: Fix triệt để lỗi `No active prompt found` khi regenerate

## ID

`EDITOR8-PROMPTS-REGENERATE-HARDENING`

## Priority

`P1 - High`

## Tóm tắt vấn đề

Endpoint regenerate hiện có thể nổ `500 Internal Server Error` với lỗi:

```text
ValueError: No active prompt found for agent_type=INTENT_ANALYZER
```

Lỗi này xảy ra khi gọi:

```text
POST /api/jobs/{job_id}/regenerate
```

và đi qua luồng:

- `regenerate_job_endpoint`
- `regenerate_job`
- `run_intent_analyzer`
- `AgentRunner.run`

## Triệu chứng quan sát được

- API trả `500` khi regenerate
- log cho thấy `Created pipeline run #2 ... (trigger=regenerate)` rồi sau đó fail
- user không nhận được lỗi cấu hình rõ ràng, chỉ thấy regenerate thất bại
- lỗi có thể chỉ lộ ra ở runtime sau khi hệ thống đã chạy bình thường với các flow khác

## Root cause

### 1. Regenerate hard-depend vào prompt `INTENT_ANALYZER`

Trong flow regenerate:

- `orchestrator.regenerate_job()` gọi `run_intent_analyzer(...)`
- `AgentRunner.run()` bắt buộc phải load được active prompt theo `agent_type`
- nếu không có active prompt, code raise `ValueError`

### 2. Cơ chế seed prompt hiện tại không đủ cho môi trường đã tồn tại từ trước

Code hiện tại seed prompt bằng logic:

- chỉ seed khi bảng `prompt_templates` hoàn toàn rỗng

Điều này tạo ra lỗ hổng vận hành:

- môi trường cũ đã có một số prompt từ trước
- codebase thêm agent mới như `INTENT_ANALYZER`
- startup không backfill prompt mới vì bảng không rỗng
- hệ thống chạy được cho đến khi chạm vào flow cần prompt mới

### 3. API không translate lỗi cấu hình thành lỗi có ý nghĩa

Hiện tại `regenerate_job_endpoint` để exception đi thẳng ra ngoài, nên user nhận raw `500` thay vì một lỗi cấu hình có thông điệp rõ ràng.

## Các file liên quan

- `../editor8/backend/src/editor8/pipeline/orchestrator.py`
- `../editor8/backend/src/editor8/pipeline/orchestrator_agents.py`
- `../editor8/backend/src/editor8/agents/runner.py`
- `../editor8/backend/src/editor8/prompts/manager.py`
- `../editor8/backend/src/editor8/prompts/seeds.py`
- `../editor8/backend/src/editor8/bootstrap.py`
- `../editor8/backend/src/editor8/api/routes.py`
- `../editor8/backend/src/editor8/health.py`

## Yêu cầu fix triệt để

### 1. Thay cơ chế `seed_if_empty` bằng cơ chế idempotent backfill

Không đủ để seed “nếu bảng rỗng”.

Cần có cơ chế kiểu:

- ensure all required seed prompts exist
- nếu thiếu prompt name nào trong `SEED_PROMPTS` thì tự thêm
- nếu prompt tồn tại nhưng không có active version thì phát hiện được
- không phá hỏng các version đang có và các A/B experiment đang chạy

Mục tiêu:

- thêm agent prompt mới không làm vỡ các DB cũ
- startup có thể tự repair phần seed mặc định còn thiếu

### 2. Thêm startup validation cho prompt catalog

Trước khi hệ thống nhận traffic hoặc trước khi worker xử lý pipeline, cần validate:

- mỗi `AgentType` bắt buộc phải có ít nhất một active prompt
- nếu thiếu, phải log rõ prompt nào đang thiếu
- health/monitoring phải phản ánh trạng thái này

Cho phép một trong hai chiến lược:

- fail fast khi startup
- hoặc startup được nhưng health chuyển degraded/error và chặn các flow liên quan

Nhưng không được để lỗi chỉ lộ ra muộn tại runtime khi user bấm regenerate.

### 3. Bổ sung cơ chế repair cho môi trường đang chạy

Cần có cách sửa các DB hiện hữu đã bị lệch seed:

- backfill command
- hoặc API/admin action an toàn
- hoặc migration script rõ ràng

Yêu cầu:

- môi trường đang thiếu `INTENT_ANALYZER` phải sửa được mà không cần xóa bảng `prompt_templates`
- không làm mất version hiện có
- không reset toàn bộ prompt catalog

### 4. Harden error handling cho regenerate

`POST /api/jobs/{job_id}/regenerate` không được trả raw `500` cho lỗi cấu hình prompt.

Cần:

- bắt lỗi thiếu prompt
- trả về lỗi có ý nghĩa, ví dụ `503` hoặc `409` kèm detail rõ ràng
- message phải chỉ ra tên prompt/agent đang thiếu
- log phải phân biệt rõ:
  - user data invalid
  - external dependency failure
  - system misconfiguration như missing prompt

### 5. Bổ sung observability cho lỗi cấu hình prompt

Health hoặc monitoring cần phản ánh:

- prompt names đang thiếu
- prompt names không có active version
- tình trạng catalog prompt có hợp lệ hay không

Ops/UI hoặc monitoring endpoint cần giúp phát hiện loại lỗi này trước khi user bấm regenerate.

### 6. Bổ sung test coverage

Cần có test cho ít nhất các case sau:

- DB không rỗng nhưng thiếu một prompt mới trong `SEED_PROMPTS`
- startup/backfill tự thêm prompt còn thiếu
- regenerate khi thiếu prompt trả lỗi có kiểm soát, không raw 500
- health phản ánh misconfiguration prompt
- mọi `AgentType` đều có active prompt sau bước ensure/backfill

## Non-goals

- không yêu cầu redesign toàn bộ prompt system
- không yêu cầu bỏ A/B testing hoặc versioning hiện tại
- không yêu cầu reset prompt catalog đang dùng trong production

## Acceptance criteria

Chỉ được coi là hoàn thành khi thỏa toàn bộ các điều kiện sau:

- môi trường có bảng `prompt_templates` không rỗng nhưng thiếu `INTENT_ANALYZER` có thể được repair an toàn
- regenerate không còn trả raw `500` vì missing active prompt
- startup hoặc health phát hiện được prompt catalog thiếu/mất active prompt
- thêm một prompt seed mới trong tương lai không làm các DB cũ âm thầm lệch cấu hình
- có test bao phủ case DB cũ thiếu prompt mới

## Gợi ý triển khai

### Prompt manager

- thêm hàm kiểu `ensure_seed_prompts(session)` thay vì chỉ `seed_if_empty(session)`
- so sánh `SEED_PROMPTS` với prompt names đang có trong DB
- insert những prompt seed còn thiếu

### Bootstrap

- đổi `seed_database()` để gọi cơ chế ensure/backfill mới
- thêm bước validate prompt catalog sau seed/backfill

### API

- translate lỗi missing prompt trong regenerate thành HTTP error có detail rõ ràng

### Monitoring

- expose trạng thái prompt catalog trong health hoặc monitoring endpoint

## Lý do cần fix ngay

Đây là lỗi cấu hình hệ thống bị che giấu:

- không lộ ra ở startup
- không lộ ra ở health hiện tại
- chỉ vỡ khi user chạm đúng flow cần prompt đó
- tạo `500` user-facing cho một lỗi có thể phát hiện sớm hơn nhiều

Nếu không fix triệt để, mỗi lần thêm agent mới hoặc seed prompt mới, hệ thống có thể tái diễn đúng pattern lỗi này trên các DB cũ.
