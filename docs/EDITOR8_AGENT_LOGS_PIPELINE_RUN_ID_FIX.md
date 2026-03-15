# Editor8 Backend Fix: `agent_logs.pipeline_run_id does not exist`

## Symptom

Backend trả `500 Internal Server Error` khi gọi `GET /api/dashboard` với lỗi:

```text
asyncpg.exceptions.UndefinedColumnError: column agent_logs.pipeline_run_id does not exist
```

## Root cause

Đây là lỗi lệch schema giữa code hiện tại và PostgreSQL đang chạy.

Các bằng chứng trong code:

- ORM hiện tại yêu cầu cột `agent_logs.pipeline_run_id` tại `backend/src/editor8/models/database.py`.
- Migration `004_add_pipeline_runs.py` thêm cột `agent_logs.pipeline_run_id` và index tương ứng.
- `Job.agent_logs` đang dùng `lazy="selectin"`, nên khi `/api/dashboard` query `Job`, SQLAlchemy tự load thêm `agent_logs`.
- `Dockerfile` và `docker-compose.yml` không tự chạy `alembic upgrade head` khi backend khởi động.
- `README.md` cũng ghi rõ migration là bước manual sau khi `docker compose up -d`.

Kết luận: container/backend đang chạy code mới hơn schema DB. Khả năng cao database đang ở revision `003` hoặc thấp hơn, hoặc volume Postgres cũ được reuse mà chưa migrate.

## Where it shows up

1. `backend/src/editor8/api/routes.py`
   Route `/api/dashboard` query danh sách `Job`.
2. `backend/src/editor8/models/database.py`
   Relationship `Job.agent_logs` dùng `lazy="selectin"`.
3. `backend/alembic/versions/004_add_pipeline_runs.py`
   Migration thêm `pipeline_run_id` vào `agent_logs`.

Vì vậy route dashboard không cần truy vấn cột này trực tiếp vẫn có thể nổ.

## Code references checked

- `../editor8/backend/src/editor8/api/routes.py:309`
  Dashboard endpoint bắt đầu tại đây.
- `../editor8/backend/src/editor8/models/database.py:54`
  `Job.agent_logs` dùng `lazy="selectin"`.
- `../editor8/backend/src/editor8/models/database.py:145`
  ORM khai báo `AgentLog.pipeline_run_id`.
- `../editor8/backend/alembic/versions/004_add_pipeline_runs.py:53`
  Migration thêm `agent_logs.pipeline_run_id`.
- `../editor8/backend/Dockerfile:14`
  Container backend chỉ start app, không chạy migrate.
- `../editor8/docker-compose.yml:18`
  Compose start backend sau Postgres healthy, nhưng không có migrate step.
- `../editor8/README.md:141`
  README yêu cầu chạy migration thủ công.

## Correct fix

### 1. Kiểm tra DB hiện tại

Từ host:

```bash
cd /home/beou/IdeaProjects/editor8
docker compose exec postgres psql -U editor8 -d editor8 -c "SELECT version_num FROM alembic_version;"
docker compose exec postgres psql -U editor8 -d editor8 -c "SELECT column_name FROM information_schema.columns WHERE table_name = 'agent_logs' ORDER BY ordinal_position;"
```

Nếu không thấy `pipeline_run_id`, schema đang bị thiếu migration.

### 2. Chạy migration lên head

Nếu service backend đang chạy từ `docker compose`:

```bash
cd /home/beou/IdeaProjects/editor8
docker compose exec backend bash -lc "cd /app && alembic upgrade head"
```

Nếu bạn đang dùng container tên `editor8-backend` như log đã cho:

```bash
docker exec -it editor8-backend bash -lc "cd /app && alembic upgrade head"
```

Nếu chạy backend local, không qua container:

```bash
cd /home/beou/IdeaProjects/editor8/backend
alembic upgrade head
```

### 3. Verify sau khi migrate

Kiểm tra lại:

```bash
cd /home/beou/IdeaProjects/editor8
docker compose exec postgres psql -U editor8 -d editor8 -c "SELECT version_num FROM alembic_version;"
docker compose exec postgres psql -U editor8 -d editor8 -c "SELECT column_name FROM information_schema.columns WHERE table_name = 'agent_logs' AND column_name = 'pipeline_run_id';"
curl -i http://localhost:8000/api/dashboard
```

Kỳ vọng:

- `alembic_version.version_num = 006`
- query cột trả về `pipeline_run_id`
- `GET /api/dashboard` trả `200 OK`

## Mandatory broader search and full remediation

Không được đóng lỗi này như một lỗi đơn lẻ. Đây phải được xử lý như một incident về schema drift.

### 1. Search toàn bộ lỗi cùng họ trong logs

Quét backend và worker để tìm thêm các lỗi kiểu thiếu cột, thiếu bảng, thiếu object:

```bash
cd /home/beou/IdeaProjects/editor8
docker compose logs backend worker --tail=2000 2>&1 | rg "Undefined(Column|Table|Object)Error|ProgrammingError|column .* does not exist|relation .* does not exist"
```

Nếu không dùng `docker compose`, thay bằng `docker logs` cho từng container tương ứng.

### 2. Audit tất cả object có nguy cơ thiếu do DB đang chậm hơn code

Sau khi đã thấy thiếu `agent_logs.pipeline_run_id`, cần kiểm tra luôn các object mới hơn trong cùng chuỗi migration:

- `pipeline_runs` table từ migration `004`
- `agent_logs.pipeline_run_id` từ migration `004`
- `agent_logs.total_tokens`, `retry_count`, `model_name`, `estimated_cost_usd` từ migration `005`
- `agent_logs.prompt_template_id` từ migration `006`
- `prompt_templates.traffic_weight` từ migration `006`

Kiểm tra tables và columns:

```bash
cd /home/beou/IdeaProjects/editor8
docker compose exec postgres psql -U editor8 -d editor8 -c "
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND (
    (table_name = 'agent_logs' AND column_name IN (
      'pipeline_run_id',
      'total_tokens',
      'retry_count',
      'model_name',
      'estimated_cost_usd',
      'prompt_template_id'
    ))
    OR (table_name = 'prompt_templates' AND column_name IN ('traffic_weight'))
    OR (table_name = 'pipeline_runs' AND column_name IN (
      'id',
      'job_id',
      'run_number',
      'trigger',
      'status',
      'started_at',
      'completed_at',
      'error_message',
      'ai_artifacts',
      'render_spec_snapshot',
      'content_hash',
      'duration_ms'
    ))
  )
ORDER BY table_name, column_name;
"
```

Kiểm tra indexes và constraints quan trọng:

```bash
cd /home/beou/IdeaProjects/editor8
docker compose exec postgres psql -U editor8 -d editor8 -c "
SELECT tablename, indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
    'ix_agent_logs_pipeline_run_id',
    'ix_agent_logs_prompt_template_id',
    'ix_pipeline_runs_job_id',
    'ix_pipeline_runs_status',
    'ix_prompt_templates_name',
    'ix_prompt_templates_active'
  )
ORDER BY tablename, indexname;
"
docker compose exec postgres psql -U editor8 -d editor8 -c "
SELECT conname, conrelid::regclass AS table_name, contype
FROM pg_constraint
WHERE conname IN (
  'fk_agent_logs_prompt_template_id',
  'uq_pipeline_run_number',
  'uq_prompt_name_version'
)
ORDER BY conname;
"
```

### 3. Fix policy cho các lỗi tương tự

Nếu audit phát hiện thiếu thêm columns, tables, indexes hoặc constraints:

- Không vá từng lỗi bằng cách sửa ORM hoặc né endpoint.
- Không thêm `ALTER TABLE` thủ công nếu chưa xác nhận rõ schema drift.
- Ưu tiên fix bằng `alembic upgrade head`.
- Sau khi migrate xong, bắt buộc chạy lại toàn bộ audit phía trên.

Nếu `alembic_version` đã là `006` nhưng object vẫn thiếu, cần coi đó là DB drift/corruption:

- kiểm tra có migration nào đã fail giữa chừng hay không
- kiểm tra có ai từng sửa schema thủ công hay không
- tạo corrective migration rõ ràng nếu thực sự cần
- không che lỗi bằng thay đổi code tạm thời

### 4. Smoke-test các endpoint có nguy cơ gặp lỗi tương tự

Các route sau có khả năng đụng các object vừa nêu, nên cần verify sau khi migrate:

- `GET /api/dashboard`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/runs`
- `GET /api/jobs/{job_id}/runs/{run_id}`
- `GET /api/jobs/{job_id}/agent-logs`
- `GET /api/agent-logs`
- `GET /api/agent-logs/stats`
- `GET /api/settings`

Lấy một `job_id` gần nhất để test:

```bash
cd /home/beou/IdeaProjects/editor8
docker compose exec postgres psql -U editor8 -d editor8 -t -A -c "SELECT job_id FROM jobs ORDER BY created_at DESC LIMIT 1;"
```

Tiêu chí pass:

- không còn `UndefinedColumnError`
- không còn `UndefinedTableError`
- không còn lỗi `relation ... does not exist`
- các endpoint trên trả `200` hoặc mã hợp lệ theo dữ liệu thực tế, nhưng không được trả `500` vì schema mismatch

### 5. Definition of done

Chỉ được coi là fix triệt để khi đồng thời thỏa tất cả điều kiện sau:

- `alembic_version` đang ở `006`
- toàn bộ columns/tables/indexes/constraints nêu ở trên đều tồn tại
- không còn lỗi schema drift trong `backend` và `worker` logs
- các endpoint liên quan không còn phát sinh `500` do schema mismatch
- đã bổ sung bước migrate vào quy trình deploy/startup để lỗi không tái diễn

## If migration does not apply cleanly

### Case 1: DB dev có thể xóa được

Nếu đây chỉ là môi trường dev và data không cần giữ:

```bash
cd /home/beou/IdeaProjects/editor8
docker compose down -v
docker compose up -d postgres
docker compose exec backend bash -lc "cd /app && alembic upgrade head"
docker compose up -d backend worker frontend
```

Lưu ý: `down -v` sẽ xóa toàn bộ dữ liệu Postgres trong volume.

### Case 2: DB cần giữ dữ liệu

Không nên sửa model để bỏ `pipeline_run_id` chỉ để khớp schema cũ. Đó là fix sai vì:

- code hiện tại đã dùng `pipeline_run_id`
- migration `004` chính thức tạo cột này
- test cũng đang kỳ vọng field này tồn tại

Trong trường hợp migration lỗi, cần xem chính xác revision hiện tại trong `alembic_version`, rồi xử lý drift theo Alembic thay vì vá tạm bằng `ALTER TABLE` thủ công.

## Prevention

Lỗi này sẽ còn lặp lại vì hiện tại deploy/startup không tự chạy migration:

- `backend/Dockerfile` chỉ chạy `python -m editor8.app`
- `docker-compose.yml` không có service migrate riêng

Nên chọn một trong hai cách:

### Option A: thêm bước migrate vào quy trình deploy

Luôn chạy:

```bash
cd /home/beou/IdeaProjects/editor8/backend
alembic upgrade head
```

trước khi restart backend/worker.

### Option B: tự động migrate khi container start

Ví dụ đổi command backend thành:

```bash
bash -lc "alembic upgrade head && python -m editor8.app"
```

và với worker:

```bash
bash -lc "alembic upgrade head && python -m editor8.worker"
```

Lưu ý: nếu làm vậy, cần đảm bảo migrate chỉ chạy sau khi Postgres healthy.

## What not to do

- Không rollback code để bỏ `pipeline_run_id` khỏi ORM.
- Không sửa riêng route dashboard để né load `agent_logs` rồi coi như xong.
- Không xóa volume production-like nếu chưa backup.

Dashboard chỉ là nơi lộ lỗi đầu tiên; gốc lỗi vẫn là schema DB đi sau code.
