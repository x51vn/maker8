# Yêu cầu cải tổ hệ thống Editor8 theo hướng nhất quán, dễ maintain, dễ mở rộng

## Mục tiêu

Chuẩn hóa Editor8 theo mô hình role separation rõ ràng:

- `editor8-backend` chỉ làm `control plane` và `CRUD API`
- `editor8-worker` chịu trách nhiệm xử lý toàn bộ pipeline async
- không còn sync action chạy pipeline trực tiếp trong API process
- chỉ hỗ trợ deployment theo mô hình Docker container để giảm drift môi trường
- CI/CD phải deploy đồng bộ đủ:
  - `editor8-frontend`
  - `editor8-backend`
  - `editor8-worker`

## Bối cảnh hiện tại

Codebase hiện tại đã tách Kafka consumer ra khỏi API app:

- API app là process riêng tại `backend/src/editor8/app.py`
- worker là process riêng tại `backend/src/editor8/worker.py`
- `docker-compose.yml` đã có service `worker`

Tuy nhiên ranh giới trách nhiệm vẫn chưa sạch:

- API vẫn trực tiếp chạy pipeline đồng bộ qua các endpoint như:
  - `POST /api/jobs/{job_id}/regenerate`
  - `POST /api/jobs/{job_id}/repick-assets`
  - `POST /api/jobs/{job_id}/approve-review`
  - `POST /api/jobs/{job_id}/update-blueprint`
- CI/CD hiện chỉ build/push/deploy `editor8-backend` và `editor8-frontend`
- workflow deploy hiện chưa pull/up `editor8-worker`
- hệ thống hiện vẫn có nhiều cách hiểu và nhiều cách chạy khác nhau, làm tăng chi phí vận hành

## Vấn đề cần giải quyết

### 1. Backend chưa thực sự là control plane

Hiện tại backend không chỉ CRUD và orchestration request handling, mà còn trực tiếp thực thi các đoạn pipeline nặng.

Điều này dẫn đến:

- API latency cao và khó dự đoán
- khó scale độc lập API và xử lý nền
- tăng rủi ro timeout, 500, và failure khi user gọi action thủ công
- khó maintain vì role của backend không rõ ràng

### 2. Worker chưa là nơi duy nhất xử lý pipeline

Nếu một phần pipeline chạy ở worker, một phần chạy trong API request cycle, hệ thống sẽ luôn tồn tại hai execution model:

- background async processing
- synchronous request-bound processing

Đây là trạng thái không nhất quán, khó mở rộng và khó debug.

### 3. Deployment topology chưa được chuẩn hóa

Hiện tại về mặt kỹ thuật có thể chạy:

- API riêng
- worker riêng
- hoặc các biến thể local/manual khác

Nếu tiếp tục cho phép nhiều kiểu deploy, hệ thống sẽ khó maintain:

- khó tái hiện lỗi
- khó viết health/ops thống nhất
- khó support đội vận hành
- dễ lệch giữa local, staging, production

### 4. CI/CD chưa deploy đầy đủ hệ thống

Workflow hiện tại mới deploy:

- `editor8-backend`
- `editor8-frontend`

Nhưng chưa deploy:

- `editor8-worker`

Điều này không chấp nhận được nếu worker là thành phần bắt buộc để hệ thống hoạt động đúng.

## Kiến trúc mục tiêu

### 1. `editor8-backend`

Vai trò duy nhất:

- expose HTTP API
- CRUD jobs, versions, prompts, settings
- validate request và ghi command/state vào DB hoặc queue
- trả về accepted/status cho client
- expose monitoring, health, ops, SSE/status APIs

Không được:

- chạy planning pipeline đồng bộ
- chạy regenerate pipeline đồng bộ
- chạy repick-assets đồng bộ
- chạy approve-review continuation đồng bộ
- thực hiện heavy AI/media processing trong request lifecycle

### 2. `editor8-worker`

Vai trò duy nhất:

- consume background jobs/commands
- xử lý toàn bộ AI pipeline
- xử lý regenerate
- xử lý repick-assets
- xử lý approve-review continuation
- publish result / emit events / update DB state

Tất cả action có side-effect nặng và thời gian xử lý đáng kể phải nằm ở worker.

### 3. `editor8-frontend`

Vai trò:

- hiển thị state từ backend
- trigger command bất đồng bộ
- theo dõi tiến trình qua polling, SSE, hoặc event APIs

Frontend không được kỳ vọng action sync kiểu “bấm nút và request chờ đến khi pipeline xong”.

## Yêu cầu thay đổi bắt buộc

### A. Loại bỏ synchronous pipeline actions khỏi API

Các endpoint sau phải được refactor thành command submission, không chạy pipeline inline:

- `POST /api/jobs/{job_id}/regenerate`
- `POST /api/jobs/{job_id}/repick-assets`
- `POST /api/jobs/{job_id}/approve-review`
- `POST /api/jobs/{job_id}/update-blueprint`

Hành vi mục tiêu:

- API nhận request
- tạo command/job nền tương ứng
- trả response nhanh, ví dụ `202 Accepted` hoặc trạng thái command đã được enqueue
- worker consume command và thực hiện xử lý

### B. Chuẩn hóa worker làm execution engine duy nhất

Mọi flow xử lý nền phải chạy trong worker, bao gồm:

- input topic processing
- regenerate
- repick-assets
- approve-review continuation
- các future background flows

Không được giữ mô hình “một số flow ở worker, một số flow sync trong backend”.

### C. Chuẩn hóa deployment: Docker-only

Chính thức hỗ trợ duy nhất mô hình deploy:

- `editor8-backend` chạy như Docker container
- `editor8-worker` chạy như Docker container
- `editor8-frontend` chạy như Docker container

Không coi các cách sau là deployment chính thức:

- chạy local process trực tiếp trên host để làm production workload
- systemd service chạy `python -m editor8.worker` ngoài container
- các kiểu deploy ad-hoc khác

Mục tiêu:

- một topology duy nhất
- dễ quan sát
- dễ rollback
- dễ maintain
- dễ onboarding

### D. Chuẩn hóa CI/CD rollout đủ 3 service

CI/CD bắt buộc phải:

1. build image cho `editor8-backend`
2. build image cho `editor8-frontend`
3. build image hoặc ít nhất deploy service `editor8-worker` từ image chuẩn
4. update deployment manifest/compose cho cả ba service
5. pull và `docker compose up -d` cho cả ba service
6. verify health của cả ba service sau deploy

Yêu cầu cụ thể:

- không được coi deployment là thành công nếu thiếu `editor8-worker`
- verify step phải kiểm tra:
  - `editor8-backend` running
  - `editor8-frontend` running
  - `editor8-worker` running

### E. Cập nhật tài liệu vận hành

README, deployment guide, troubleshooting phải phản ánh đúng:

- backend là control plane CRUD-only
- worker là execution plane duy nhất
- action người dùng chỉ enqueue background work
- deployment chuẩn là Docker-only
- CI/CD deploy đủ cả frontend/backend/worker

### F. Cập nhật observability theo kiến trúc mới

Health và ops phải thể hiện rõ 3 role:

- frontend availability
- backend API availability
- worker availability

Ngoài ra cần có tín hiệu cho:

- worker last heartbeat
- background queue backlog hoặc stuck commands
- command failure rate
- action async đang chạy / chờ / fail

## Thay đổi CI/CD hiện tại cần thực hiện

Workflow `editor8/.github/workflows/ci-cd.yml` cần được cập nhật để:

- không chỉ pull `editor8-backend` và `editor8-frontend`
- phải pull thêm `editor8-worker`
- phải `docker compose up -d` thêm `editor8-worker`
- phải verify trạng thái `editor8-worker`

Nếu dùng chung image backend cho worker:

- vẫn phải coi `editor8-worker` là service deploy độc lập
- không được bỏ qua chỉ vì dùng chung image

Nếu tách image riêng cho worker:

- pipeline phải build/push image `editor8-worker`
- deployment repo phải update tag cho worker riêng

## Nguyên tắc thiết kế sau cải tổ

- một role, một trách nhiệm rõ ràng
- API không giữ request mở để chờ pipeline xử lý nặng
- worker là nơi duy nhất thực thi side-effect dài và phức tạp
- deployment chỉ có một topology chính thức
- monitoring phản ánh đúng topology chính thức

## Acceptance criteria

Chỉ được coi là hoàn thành khi thỏa toàn bộ điều kiện sau:

- backend không còn chạy pipeline sync cho regenerate/repick/approve-review/update-blueprint
- các action trên trở thành background command được worker xử lý
- worker là execution engine duy nhất cho mọi flow async
- deployment chính thức chỉ còn Docker container topology
- CI/CD deploy và verify đủ `editor8-frontend`, `editor8-backend`, `editor8-worker`
- docs mô tả đúng kiến trúc mới
- ops/health thể hiện được worker status và background processing status

## Checklist triển khai

### Backend/API

- refactor các action sync sang command submission
- trả response async-friendly
- thêm command status model nếu cần

### Worker

- thêm handling cho command types mới:
  - regenerate
  - repick-assets
  - approve-review
  - update-blueprint continuation nếu vẫn cần background

### Deployment

- chuẩn hóa compose/deployment manifest đủ 3 service
- bỏ các nhánh deploy ngoài Docker khỏi tài liệu chính thức

### CI/CD

- build/push/deploy/verify cả 3 service
- fail deployment nếu thiếu worker

### Docs

- cập nhật README
- cập nhật deployment docs
- cập nhật troubleshooting
- cập nhật ops runbook

## Lý do cần làm

Đây là bước cần thiết để hệ thống:

- nhất quán hơn
- dễ maintain hơn
- dễ scale hơn
- dễ debug hơn
- dễ mở rộng thêm background flows hơn

Nếu không phân định dứt điểm, Editor8 sẽ tiếp tục ở trạng thái hybrid:

- role mơ hồ
- flow xử lý phân tán
- deployment dễ thiếu thành phần
- CI/CD không phản ánh hệ thống thực tế
- đội vận hành khó tin cậy vào behavior của hệ thống
