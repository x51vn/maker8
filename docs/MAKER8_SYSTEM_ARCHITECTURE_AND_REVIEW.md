# Maker8 System Architecture And Review

## 1. Purpose

`maker8` là render worker của hệ thống `editor8 -> maker8`.

Nó không phải là một API service. `maker8` là một tiến trình nền tiêu thụ Kafka message `video.render.request.v1`, dựng asset cục bộ, synthesize TTS, render video bằng MoviePy/FFmpeg, upload output lên Dropbox, rồi phát kết quả lại về Kafka.

Mục tiêu của tài liệu này là:

- mô tả đúng cách `maker8` vận hành ở runtime hiện tại
- làm rõ các thành phần chính và ranh giới với `editor8`
- chỉ ra các tech debt và inconsistency giữa code, docs, contract và deployment
- đề xuất hướng cải thiện để hệ thống dễ maintain, dễ debug, và dễ mở rộng hơn

## 2. High-Level Architecture

```text
editor8
  -> Kafka: video.render.request.v1
    -> maker8 RenderConsumer
      -> Orchestrator
        -> VALIDATE
        -> RESOLVE_ASSETS
        -> DOWNLOAD
        -> NORMALIZE
        -> TTS
        -> RENDER
        -> UPLOAD_DROPBOX
        -> EMIT_RESULT
  -> Kafka: video.render.result.v1
  -> Kafka: video.render.dlq.v1
```

### Core runtime characteristics

- `maker8` là worker đồng bộ: một process xử lý một job tại một thời điểm.
- Mỗi job có work directory riêng tại `MAKER8_WORK_DIR/<job_id>/`.
- Mỗi stage thao tác trên `PipelineContext`; artifacts trung gian nằm trên local disk.
- Retry hiện là per-stage, không phải per-job toàn cục.
- Output transport thực tế là Kafka result topic + Dropbox artifacts.

## 3. Main Components

### 3.1 Application bootstrap

Entry point hiện tại là [`src/maker8/app.py`](../src/maker8/app.py).

Vai trò:

- load `Settings` từ env
- setup structured logging
- probe GPU capability
- khởi tạo health/worker state
- khởi tạo Kafka producer
- load plugin registry
- khởi tạo TTS service và Dropbox client
- khởi tạo `Orchestrator`
- khởi tạo `RenderConsumer`
- block trong vòng lặp consume

Điểm quan trọng:

- `maker8` không expose HTTP API chính thức
- metrics server chỉ là optional side capability
- shutdown path dùng `os._exit(0)` để tránh treo ở cleanup của native libs

### 3.2 Consumer

Consumer nằm ở [`src/maker8/kafka/consumer.py`](../src/maker8/kafka/consumer.py).

Vai trò:

- subscribe Kafka topic đầu vào
- poll message
- parse JSON payload
- gọi `handler(payload)` đồng bộ
- commit offset

Operational reality:

- consumer này block trong lúc job đang chạy
- `max.poll.interval.ms` được tăng cao để chịu được pipeline dài
- worker hiện scale theo số instance, không theo thread/concurrency nội bộ

### 3.3 Orchestrator

Orchestrator nằm ở [`src/maker8/pipeline/orchestrator.py`](../src/maker8/pipeline/orchestrator.py).

Vai trò:

- parse `RenderRequest`
- tạo `PipelineContext`
- chạy stage chain theo thứ tự cố định
- áp retry policy
- emit failed result và DLQ khi stage fail
- cleanup work directory

Đây là control plane nội bộ của worker. Mọi logic điều phối stage đều đi qua đây.

### 3.4 PipelineContext

`PipelineContext` ở [`src/maker8/pipeline/context.py`](../src/maker8/pipeline/context.py) là object trung tâm chảy xuyên suốt toàn bộ pipeline.

Nó giữ:

- `job_id`, `job_key`, `trace`
- directories của job
- resolved plans
- downloaded assets
- normalized assets
- TTS results
- rendered output
- Dropbox refs
- output metadata
- asset report
- retry attempt

Điểm này đúng về mặt thực dụng, nhưng cũng làm cho pipeline phụ thuộc mạnh vào local filesystem state.

### 3.5 Stages

Stage order hiện tại:

1. `VALIDATE`
2. `RESOLVE_ASSETS`
3. `DOWNLOAD`
4. `NORMALIZE`
5. `TTS`
6. `RENDER`
7. `UPLOAD_DROPBOX`
8. `EMIT_RESULT`

Mỗi stage có trách nhiệm khá rõ:

- `VALIDATE`: validate request/spec, tính `job_key`
- `RESOLVE_ASSETS`: map asset sang connector-specific plan
- `DOWNLOAD`: tải asset về local disk
- `NORMALIZE`: đưa media về format dễ xử lý cho downstream
- `TTS`: tạo narration audio per scene
- `RENDER`: compose scene và encode file output
- `UPLOAD_DROPBOX`: upload video + manifest
- `EMIT_RESULT`: emit `RenderResult`

### 3.6 Plugins and connectors

Plugin registry ở [`src/maker8/plugins/registry.py`](../src/maker8/plugins/registry.py).

Built-in source connectors hiện tại:

- `youtube`
- `http`

Built-in effect plugins hiện tại gồm blur, fade, rotate, zoom/pan, grayscale, slide, chroma-key và một số effect cơ bản khác.

Nhìn chung kiến trúc plugin là đúng hướng. Tuy vậy phần lớn vận hành hiện tại vẫn phụ thuộc vào hai connector đầu vào và MoviePy composition core.

### 3.7 Rendering stack

Rendering stack hiện tại là hybrid:

- scene composition: MoviePy
- asset probing / media transforms: FFmpeg
- output encoding: FFmpeg qua `write_videofile`
- GPU selection logic: [`src/maker8/rendering/encoder.py`](../src/maker8/rendering/encoder.py)

Điểm cần hiểu đúng:

- có GPU encoder không đồng nghĩa toàn bộ pipeline render chạy trên GPU
- phần compose clip, layer building, transition handling vẫn phụ thuộc nhiều vào CPU và MoviePy
- GPU hiện chủ yếu giúp encode hoặc một số nhánh normalize, không biến `maker8` thành GPU-native renderer

## 4. End-To-End Job Lifecycle

### 4.1 Input boundary

Input contract là `RenderRequest`.

`maker8` hiện đã dùng canonical contract từ `render_contracts` thay vì giữ model riêng lệch chuẩn. Điều này là hướng đúng cho boundary giữa `editor8` và `maker8`.

Input thực tế gồm:

- `job_id`
- `spec_version`
- `render_spec`
- `result`
- `trace`

### 4.2 Validation and canonicalization

Khi nhận message:

- consumer parse JSON
- orchestrator validate payload thành `RenderRequest`
- `VALIDATE` kiểm tra shape, referential integrity, canvas constraints, scene/assets uniqueness
- `job_key` được tính để hỗ trợ canonical identity của render job

### 4.3 Asset pipeline

Asset flow hiện tại:

1. resolve từng asset qua connector
2. download asset về `assets/`
3. normalize video/audio nếu cần
4. ghi path kết quả vào `PipelineContext`

Điểm mạnh:

- stage tách bạch
- log đã khá hơn trước

Điểm yếu:

- artifacts trung gian trên disk trở thành một phần implicit của state machine
- nếu cleanup, retry, validation artifact không chặt thì downstream dễ đọc nhầm file hỏng

### 4.4 TTS pipeline

TTS lấy narration per scene, resolve preset, rồi synthesize audio cục bộ.

Thiết kế hiện tại:

- scene-level override
- default-level fallback
- support key rotation cho một số provider

Điểm yếu thực tế:

- đây là dependency mạng và credential nhạy cảm
- khi lỗi, operator cần nhiều logs hơn là chỉ biết stage `TTS` fail

### 4.5 Render pipeline

Render stage:

- lấy normalized assets nếu có, fallback về downloaded assets
- build scene clips
- mix audio tracks + narration
- apply effect plugins
- concatenate scenes
- encode output `.mp4`

Runtime reality:

- đây là stage nặng nhất
- đây cũng là stage khó quan sát nhất nếu không có progress logs
- encode có thể dùng GPU, nhưng scene composition vẫn không hoàn toàn offload lên GPU

### 4.6 Result pipeline

Sau khi render xong:

- upload output và manifest lên Dropbox
- build `RenderResult`
- emit về Kafka result topic

Nếu fail:

- emit `FAILED RenderResult`
- emit `DLQPayload`

## 5. Operational Model

### 5.1 Deployment assumptions

`maker8` được thiết kế như một worker process/container riêng.

Nó cần các dependency runtime sau:

- Kafka
- FFmpeg
- yt-dlp
- Dropbox credentials
- TTS credentials hoặc provider tương ứng
- local disk đủ lớn cho workdir
- optional NVIDIA runtime nếu muốn GPU acceleration

### 5.2 Concurrency model

Concurrency model hiện tại rất đơn giản:

- một instance tiêu thụ một message tại một thời điểm
- throughput tăng bằng cách tăng số worker instance
- một job dài sẽ chiếm trọn instance đó

Ưu điểm:

- code dễ reason hơn
- debug một job trên một worker đơn giản hơn

Nhược điểm:

- backpressure mạnh
- retry sleep giữ instance ở trạng thái chờ
- long-running render làm giảm throughput thấy rõ

### 5.3 Health and observability

Repo hiện có:

- liveness file
- readiness file
- status JSON abstraction
- structured logs
- optional Prometheus metrics

Nhưng về thực tế vận hành:

- logs mới là nguồn chẩn đoán chính
- status/health layer vẫn chưa được gắn chặt vào runtime flow như đáng ra phải có
- deployment có nguy cơ drift với health semantics mới nếu vẫn check file cũ

### 5.4 Failure handling

Failure model hiện tại là stage-based:

- stage raise `StageError`
- orchestrator log failure
- nếu retryable thì backoff
- nếu không retryable hoặc exhausted thì emit `FAILED` + `DLQ`

Đây là mô hình hợp lý. Vấn đề nằm ở chỗ classification và artifact hygiene chưa đủ chặt ở một số nhánh.

## 6. Current Strengths

Những điểm tốt hiện tại:

- Kiến trúc worker tương đối rõ: consumer, orchestrator, stages.
- Boundary contract với `editor8` đã đi đúng hướng nhờ `render_contracts`.
- Pipeline chia stage hợp lý, giúp log và retry có cấu trúc hơn.
- Plugin architecture đủ tốt để mở rộng source/effect.
- Logging đã tiến bộ rõ ở download, normalize, resolve, TTS.
- GPU capability detection đã có nền tảng.
- Test đã có cho contract compatibility, observability helpers và một số edge case quan trọng.

## 7. Tech Debt And Inconsistencies

Đây là phần quan trọng nhất của review hiện tại.

### 7.1 Consumer commit semantics đang lệch với docstring và ý nghĩa an toàn

`RenderConsumer` ghi rõ là manual commit sau successful handling, nhưng code lại commit offset trong `finally`.

Hệ quả:

- handler fail vẫn có thể commit offset
- malformed payload hoặc unexpected exception có thể bị mất mà không reprocess
- tài liệu và hành vi runtime không khớp nhau

Đây là một inconsistency vận hành nghiêm trọng.

### 7.2 Invalid payload path còn yếu

Trong orchestrator, payload invalid hiện chỉ log rồi `return` vì không parse được `job_id`.

Hệ quả:

- không có DLQ record giàu ngữ cảnh cho loại lỗi này
- operator phải dựa vào log line thay vì failure artifact chính thức

### 7.3 Result destination semantics không khớp contract

`RenderRequest.result` tồn tại trong contract, nhưng `EMIT_RESULT` hiện luôn phát ra configured topic và dùng `job_id` làm key.

Hệ quả:

- field có trong schema nhưng không điều khiển runtime như tên gọi gợi ý
- producer side có thể tưởng `topic` và `key` được tôn trọng, nhưng consumer side bỏ qua

### 7.4 CONTRACT_FIELD_STATUS.md đang bị stale ở vài chỗ quan trọng

Ví dụ:

- `AssetSource.options` được đánh dấu `RESERVED`
- nhưng `youtube` connector thực tế dùng `options.format`

Điều này làm operator hoặc developer đọc tài liệu contract hiện tại có thể hiểu sai hệ thống.

### 7.5 README và runtime hiện tại chưa khớp hoàn toàn

Một số điểm drift:

- retryability trong README không còn khớp với `RetryPolicy` hiện tại
- observability section trong README chưa phản ánh hết health/status/log evolution gần đây
- GPU behavior thực tế phức tạp hơn tài liệu mô tả

README hiện vẫn hữu ích, nhưng không còn là nguồn sự thật đầy đủ.

### 7.6 Observability layer mới có cấu trúc nhưng chưa fully wired

`WorkerState` và `HealthManager` đã tồn tại, nhưng `flush_status()` hiện gần như chỉ được dùng trong test.

Hệ quả:

- repo có abstraction tốt nhưng runtime value chưa khai thác đủ
- operator chủ yếu vẫn phải bám `docker logs`
- status JSON có tiềm năng nhưng chưa thực sự trở thành source-of-truth cho trạng thái hiện tại

### 7.7 Health semantics có nguy cơ drift với deployment

Code hiện quản lý:

- `/tmp/maker8_live`
- `/tmp/maker8_ready`
- `/tmp/maker8_status.json`

Nếu deployment vẫn check file cũ hoặc assumption cũ, container có thể bị đánh dấu unhealthy sai.

Đây là inconsistency giữa code và deployment, không phải chỉ giữa code và docs.

### 7.8 Artifact integrity giữa NORMALIZE và RENDER từng có lỗ hổng rõ ràng

Incident gần đây cho thấy:

- normalize có thể bị `SIGKILL`
- file `_norm.mp4` partial/corrupt có thể trở thành input downstream
- render fail với `moov atom not found`

Code đã được cải thiện theo hướng validate và purge artifact hỏng hơn trước, nhưng incident đó cho thấy toàn bộ hệ thống phụ thuộc rất mạnh vào filesystem hygiene.

Đây là debt kiến trúc thực tế của pipeline disk-based.

### 7.9 GPU capability và GPU usage thực tế chưa luôn nhất quán

Hệ thống hiện đã:

- probe `nvidia-smi`
- probe NVENC
- resolve encoder theo availability

Nhưng runtime incidents vẫn cho thấy có lúc pipeline rơi về `libx264` CPU path dù môi trường có GPU.

Điều này cho thấy:

- detection capability chưa đồng nghĩa deterministic usage
- fallback path và deployment/runtime consistency vẫn cần siết lại

### 7.10 Rendering stack vẫn bị giới hạn bởi MoviePy-first architecture

Ngay cả khi encode dùng GPU:

- layer composition
- scene building
- effect application
- một phần audio/video manipulation

vẫn gắn chặt vào MoviePy.

Điều đó giới hạn trần hiệu năng và làm quá trình render khó predict hơn so với một pipeline FFmpeg-native hoàn chỉnh.

### 7.11 Producer flush cho từng message

`KafkaProducer.send()` gọi `flush()` ngay sau mỗi `produce()`.

Điều này đơn giản và an toàn theo kiểu blocking, nhưng:

- giảm throughput
- tăng latency
- làm producer khó scale nếu sau này worker phát nhiều event hơn

### 7.12 Test coverage vẫn thiên về model/helper hơn là runtime pipeline thật

Hiện có test tốt cho:

- contracts
- observability helpers
- một số edge case như youtube format handling

Nhưng còn thiếu:

- end-to-end pipeline tests
- integration tests với FFmpeg/yt-dlp runtime behavior
- failure-mode tests cho cleanup, retry, corrupt artifacts, upload failures

Đây là một khoảng trống quan trọng.

### 7.13 Documentation đang bị phân mảnh

Repo đang có nhiều file docs nhỏ phục vụ incident review và request card.

Điều đó hữu ích cho tracking, nhưng cũng tạo ra vấn đề:

- thông tin bị rải rác
- khó biết đâu là tài liệu nền tảng
- operator mới vào dự án dễ đọc nhầm file cũ hoặc file mang tính tạm thời

## 8. Recommended Documentation Structure

Để tài liệu của `maker8` rõ ràng và bền hơn, nên tách theo 4 lớp:

1. `README.md`
   - mô tả ngắn gọn project, quick start, high-level architecture
2. `docs/MAKER8_SYSTEM_ARCHITECTURE_AND_REVIEW.md`
   - tài liệu nền tảng về kiến trúc, vận hành, giới hạn, tech debt
3. `docs/KAFKA_INTEGRATION.md`
   - chỉ tập trung vào wire contract, topics, failure semantics
4. incident / request docs
   - chỉ dùng cho tracking thay đổi hoặc điều tra sự cố cụ thể

Ngoài ra nên xác định rõ:

- file nào là source of truth cho kiến trúc
- file nào chỉ là incident note

## 9. Recommended Engineering Priorities

### Priority 0

- sửa consumer commit semantics để offset chỉ được commit khi flow xử lý phù hợp
- đồng bộ healthcheck deployment với health semantics hiện tại
- đảm bảo corrupt normalized artifact không thể bị reuse
- làm rõ và chuẩn hóa failure classification cho operator

### Priority 1

- đồng bộ lại README, contract status doc, runtime behavior
- wire `WorkerState` / `status.json` vào runtime thật sự
- siết deterministic GPU usage và log rõ fallback reasons
- bổ sung integration tests cho normalize/render/upload paths

### Priority 2

- giảm phụ thuộc vào implicit filesystem state
- cải thiện batching / flush strategy cho Kafka producer
- xem xét lâu dài việc giảm phụ thuộc vào MoviePy cho những phần performance-critical

## 10. Render Performance Review (2026-04-18)

Sau khi review lại render path hiện tại, các điểm có impact hiệu năng lớn nhất là:

### 10.1 Bottleneck hiện tại

- Phần compose vẫn chủ yếu là MoviePy-first, nên CPU mới là nút cổ chai chính chứ không phải encode.
- Download, normalize và TTS đang chạy tuần tự theo từng asset hoặc từng scene, nên một job dài tăng wall-clock gần tuyến tính.
- Mỗi layer video mở lại file media riêng trong rendering layer builder, làm tăng I/O và chi phí decode khi cùng asset được dùng lặp lại.
- Performance profile đã có sẵn nhưng trước đây preset CPU/GPU chưa được truyền đầy đủ vào encoder path, nên chế độ fast chưa tận dụng hết phần giảm thời gian encode.

### 10.2 Cải thiện nên ưu tiên ngay

1. Ưu tiên scene-level render và FFmpeg-native effects hơn nữa để giảm graph MoviePy lớn trong memory.
2. Chạy song song có giới hạn cho download, normalize và TTS theo asset hoặc scene, với semaphore theo tài nguyên CPU/GPU/disk.
3. Cache clip handle và text rasterization theo asset hoặc nội dung để tránh mở lại cùng file và render lại cùng text nhiều lần.
4. Giữ proxy downscale mặc định ở balanced hoặc fast cho job social-video; chỉ dùng full-res cho job thật sự cần chất lượng cao.
5. Đo riêng build time và encode time cho từng scene để quyết định scene nào nên đi CPU path, scene nào nên đi GPU path.

### 10.3 Hướng kiến trúc dài hạn

- Tách render planner khỏi render executor để planner sinh ra execution plan tối ưu theo profile.
- Dịch dần các effect phổ biến từ Python clip ops sang FFmpeg filter graph hoặc pre-baked templates.
- Thêm cache artifact theo content hash cho normalized asset và TTS output để các job lặp không phải làm lại từ đầu.
- Cân nhắc worker pool theo loại tải: một pool cho ingest/TTS và một pool riêng cho render GPU-heavy.

## 11. Bottom Line

`maker8` hiện là một worker render hoạt động được và có nền tảng kiến trúc tương đối tốt: stage pipeline rõ ràng, contract đang được chuẩn hóa, logging đã tốt hơn, plugin model đủ dùng, và GPU support đã bắt đầu hình thành.

Tuy vậy, hệ thống vẫn còn một số debt mang tính vận hành và tính nhất quán:

- docs chưa theo kịp runtime
- một số contract semantics không khớp behavior thực tế
- consumer safety semantics còn sai
- observability abstraction có nhưng chưa fully wired
- GPU support có nhưng chưa đủ deterministic
- end-to-end reliability còn phụ thuộc nhiều vào filesystem artifacts và incident-driven fixes

Nếu cần một kết luận ngắn gọn: `maker8` đã qua giai đoạn prototype, nhưng chưa đạt trạng thái production-hardening hoàn chỉnh. Việc ưu tiên nhất bây giờ không phải thêm feature mới, mà là làm cho behavior, contract, docs và deployment khớp nhau một cách nhất quán.
