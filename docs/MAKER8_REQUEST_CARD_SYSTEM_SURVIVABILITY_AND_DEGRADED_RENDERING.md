# Maker8 Request Card: System Survivability And Degraded Rendering

## 1. Summary

`maker8` cần được harden theo hướng:

- một asset có thể fail nhưng job vẫn có khả năng hoàn thành ở chế độ degraded
- một stage có thể fail trên một input cụ thể nhưng worker không được mất khả năng xử lý các job tiếp theo
- GPU path có thể fail trên một asset nhưng pipeline không được suy sụp theo kiểu domino
- hệ thống phải tiếp tục hoạt động và tiếp tục sản xuất video, thay vì sập toàn job hoặc sập toàn worker vì một lỗi cục bộ

Đây không còn là yêu cầu “fix một bug normalize”. Đây là yêu cầu nâng cấp `maker8` sang mô hình **survivable pipeline**.

## 2. Context

Incident gần đây cho thấy chuỗi failure như sau:

1. một asset video normalize thành công bằng `h264_nvenc`
2. asset khác fail ở GPU normalize
3. pipeline fallback sang `libx264`
4. CPU fallback bị `SIGKILL`
5. cả stage `NORMALIZE` fail
6. cả job bị chặn bởi một asset duy nhất

Điều này cho thấy:

- GPU capability ở mức process không đồng nghĩa mọi asset đều chạy tốt trên GPU path
- CPU fallback hiện không đủ an toàn khi môi trường thiếu tài nguyên
- pipeline hiện đang có xu hướng “all-or-nothing” quá mạnh
- survivability của worker và survivability của job chưa được thiết kế rõ ràng

## 3. Problem Statement

`maker8` hiện vẫn còn mang tư duy:

- nếu một asset quan trọng fail thì cả job fail
- nếu một stage fail thì retry stage đó theo policy chung
- nếu GPU fail thì rơi sang CPU fallback và hy vọng nó sống

Cách tiếp cận này không đủ tốt cho production pipeline.

Trong hệ thống render thực tế, **failure cục bộ là trạng thái bình thường**, không phải ngoại lệ hiếm:

- một nguồn video có thể corrupt
- một asset có thể quá nặng cho GPU decode
- CPU fallback có thể bị OOM
- một scene có thể chứa asset không tương thích
- một connector có thể bị rate limit

Nếu mọi lỗi cục bộ đều dẫn đến `FAILED` toàn job, hệ thống sẽ:

- giảm throughput mạnh
- gây queue backlog
- làm worker mất thời gian retry vô ích
- khiến operator bị ngập trong failed results
- khó đạt mục tiêu “liên tục sản xuất video”

## 4. Target Outcome

Mục tiêu cần đạt là:

### 4.1 Worker survivability

- một job fail không được làm worker mất khả năng xử lý job tiếp theo
- một process con như `ffmpeg` hay `yt-dlp` bị kill không được kéo theo state hỏng cho worker
- worker phải recover sạch sau mỗi failure path

### 4.2 Job survivability

- một asset fail không mặc định kéo theo fail toàn job
- một scene fail không mặc định kéo theo fail toàn video
- nếu vẫn còn khả năng produce video hợp lệ, job phải hoàn thành ở chế độ degraded

### 4.3 Throughput survivability

- retry không được chiếm worker quá lâu cho các lỗi deterministic
- resource starvation trên một job không được làm toàn hệ thống ngừng sản xuất
- system phải tiếp tục xử lý các job khác ngay cả khi có một số job khó hoặc corrupt

## 5. Non-Negotiable Principles

### 5.1 Fail small, not fail global

Lỗi phải được cô lập ở mức nhỏ nhất có thể:

- process-level
- asset-level
- scene-level
- job-level

Không được để một lỗi asset-level tự động leo thành system-level failure.

### 5.2 Degrade gracefully

Nếu không thể render “đầy đủ”, hệ thống phải cố render “đủ dùng”.

Ví dụ:

- mất một background video nhưng vẫn còn narration và text
- mất một source video nhưng có thể dùng image placeholder
- mất một scene nhưng vẫn có thể render các scene còn lại

### 5.3 Deterministic failures must not be retried blindly

Các lỗi như:

- unsupported codec/path
- invalid media
- deterministic NVENC incompatibility
- container memory limit không đủ cho CPU fallback

không được retry theo cùng một cách vô hạn hoặc lặp lại vô ích.

### 5.4 Partial success must be explicit

Nếu video được sản xuất với degradation:

- phải có trạng thái rõ ràng
- phải có warning list rõ ràng
- operator phải biết asset nào bị drop/fallback

Không được “DONE giả” và cũng không nên “FAILED toàn phần” khi output vẫn dùng được.

## 6. Required Changes

## 6.1 Introduce explicit degraded-output semantics

Hệ thống phải bổ sung semantic rõ cho job thành công nhưng có degradation.

Yêu cầu:

- thêm một trạng thái như `DONE_WITH_WARNINGS`, `DEGRADED`, hoặc giữ `DONE` nhưng kèm `warnings[]` bắt buộc
- `RenderResult` phải chứa:
  - danh sách asset/scene bị fail
  - fallback path đã dùng
  - mức độ ảnh hưởng tới output
  - output có đầy đủ hay degraded

Không được chỉ có hai trạng thái nhị phân `DONE` và `FAILED` nếu pipeline thực tế đã hỗ trợ degrade.

## 6.2 Move from global GPU decision to per-asset execution strategy

Hiện tại `maker8` suy luận:

- process có NVENC
- vậy asset video sẽ chạy GPU normalize path

Điều này là quá thô.

Yêu cầu:

- quyết định normalize strategy theo từng asset
- trước khi normalize phải probe media stream:
  - codec
  - container
  - resolution
  - pix_fmt
  - audio/video stream presence
- chọn strategy per asset:
  - GPU decode + GPU encode
  - CPU decode + GPU encode
  - CPU-only
  - skip normalization nếu asset đã compatible

Không được dùng một decision duy nhất cho tất cả asset trong job.

## 6.3 Add asset-level fallback hierarchy

Mỗi asset cần có fallback policy.

Ví dụ với video asset:

1. GPU normalize
2. CPU decode + GPU encode
3. CPU-only normalize nếu resource budget cho phép
4. reuse source file nếu đã probe compatible cho render
5. replace bằng placeholder asset
6. drop asset khỏi scene nhưng vẫn giữ scene
7. drop scene nhưng vẫn hoàn thành video nếu policy cho phép

Fallback hierarchy phải là code policy rõ ràng, không phải xử lý ad-hoc theo incident.

## 6.4 Add scene-level and job-level degradation policy

Khi một asset fail, hệ thống phải quyết định:

- scene có render được không nếu bỏ asset đó
- có thể dùng background màu tĩnh + text + narration không
- có thể ghép các scene còn lại thành video hợp lệ không

Yêu cầu:

- define rule rõ cho scene survivability
- define minimum renderable output
- define lúc nào bắt buộc fail toàn job

Ví dụ:

- nếu còn ít nhất một scene renderable và output hợp lệ, job nên được phát hành ở chế độ degraded
- chỉ fail toàn job khi không còn cách produce output hợp lệ nào

## 6.5 Protect worker from heavy or toxic jobs

Worker không được bị kéo chết bởi một asset/video xấu.

Yêu cầu:

- hard timeout per subprocess
- hard timeout per stage
- memory-aware classification cho `SIGKILL`, OOM, cgroup kill
- cleanup triệt để artifact partial trên mọi nhánh failure
- không reuse artifact hỏng
- nếu một job bị classify là toxic, phải quarantine hoặc fail-fast thay vì retry dài

## 6.6 Make retry policy resource-aware and error-aware

Retry hiện tại chưa đủ phân biệt giữa:

- transient infra error
- deterministic asset incompatibility
- resource exhaustion
- operator/deployment issue

Yêu cầu:

- retry chỉ áp cho lỗi thực sự transient
- `FFMPEG_KILLED` phải được phân loại sâu hơn:
  - OOM/cgroup/resource exhaustion
  - operator kill
  - node pressure
- deterministic GPU incompatibility không được retry cùng một GPU path
- CPU fallback bị `SIGKILL` không được retry mù 5 lần trên cùng worker profile

## 6.7 Make worker state and health reflect reality

Health không được chỉ là “process còn sống”.

Yêu cầu:

- state file phải phản ánh:
  - current job
  - current stage
  - current asset
  - fallback strategy hiện tại
  - degraded mode có đang kích hoạt hay không
  - retry sleep reason
- health/readiness phải tách biệt:
  - process alive
  - worker ready nhận job mới
  - worker degraded nhưng vẫn phục vụ được

## 6.8 Keep the system producing videos, not just logging failures

Mục tiêu của pipeline là sản xuất output, không chỉ fail có cấu trúc hơn.

Yêu cầu:

- ưu tiên tạo output degraded hợp lệ nếu còn khả năng
- operator có thể cấu hình policy:
  - strict mode: fail nếu thiếu asset bắt buộc
  - survivable mode: continue với fallback
- default production policy nên nghiêng về survivable mode

## 7. Best Practices That Must Be Applied

## 7.1 Bulkhead isolation

Không để một asset/process nặng làm lây lan failure sang cả worker.

Áp dụng:

- subprocess isolation
- per-job workdir isolation
- strict cleanup
- bounded retries

## 7.2 Graceful degradation by design

Fallback phải là một phần thiết kế chính thức, không phải hack tạm.

Áp dụng:

- placeholder assets
- scene skipping policy
- output warnings
- partial success semantics

## 7.3 Explicit resource budgeting

Mọi strategy phải có budget:

- CPU budget
- memory budget
- GPU budget
- timeout budget
- retry budget

Không được fallback vô điều kiện sang chiến lược đắt hơn mà không biết worker có chịu nổi hay không.

## 7.4 Per-asset decision making

Media pipeline phải quyết định theo asset, không theo assumption toàn cục.

Áp dụng:

- ffprobe-first
- strategy selection per asset
- stream-aware compatibility checks

## 7.5 Do not retry deterministic breakage

Nếu cùng một asset, cùng một strategy, cùng một environment chắc chắn sẽ fail lại, không được retry chỉ để “hy vọng”.

## 7.6 Quarantine toxic inputs

Những job/asset gây:

- OOM
- repeated corrupt artifacts
- repeated deterministic incompatibility

phải bị quarantine hoặc fail-fast với classification rõ, thay vì tiếp tục phá throughput chung.

## 7.7 Idempotent cleanup and artifact hygiene

Không được để partial output ảnh hưởng run sau.

Áp dụng:

- xóa artifact partial trên mọi nhánh error
- verify artifact trước khi reuse
- không dựa vào “file exists” như bằng chứng artifact hợp lệ

## 7.8 Operator-visible degradation

Operator phải biết:

- asset nào bị drop
- scene nào bị degrade
- video nào là partial success
- fallback nào được dùng

Không được che degradation dưới một status `DONE` mơ hồ.

## 7.9 Observability must support action, not just postmortem

Logs và status phải giúp operator ra quyết định ngay:

- continue
- quarantine
- scale worker
- tune resource limit
- fix bad source

Không chỉ phục vụ phân tích sau sự cố.

## 8. Required Deliverables

## 8.1 Design deliverables

- tài liệu hóa degradation policy ở mức asset/scene/job
- ma trận retry/fallback theo error class
- definition rõ cho partial success output

## 8.2 Code deliverables

- per-asset normalize strategy selector
- fallback hierarchy implementation
- degraded render support
- resource-aware failure classification
- stronger artifact validation and cleanup
- worker state/status wiring đầy đủ

## 8.3 Contract deliverables

- update result contract để phản ánh degraded success
- update docs giữa `editor8` và `maker8`
- đảm bảo upstream hiểu được output có warning/degradation

## 8.4 Test deliverables

Phải có automated tests cho các case sau:

- GPU path fail nhưng CPU-compatible path vẫn produce output
- một asset fail nhưng scene vẫn render được
- một scene fail nhưng video cuối vẫn được tạo
- toxic asset bị quarantine/fail-fast, worker vẫn xử lý job sau bình thường
- partial artifact không bị reuse
- degraded result có warnings đầy đủ

## 9. Definition Of Done

Chỉ được coi là hoàn thành khi:

- một asset video fail không mặc định kéo fail toàn job
- worker vẫn sống và tiếp tục xử lý các job tiếp theo sau incident
- output degraded được emit với semantic rõ ràng
- retry không còn lặp vô ích với deterministic failures
- operator có thể nhìn logs/status và biết chính xác vì sao job degraded hoặc failed
- CI có test khóa behavior survivability

## 10. Final Requirement

Yêu cầu cốt lõi của card này là:

> `maker8` phải được thiết kế như một hệ thống có khả năng sống sót trước lỗi cục bộ.

Nói cụ thể hơn:

- một video source có thể hỏng
- một asset có thể không tương thích
- một stage có thể fail trên một input
- một fallback có thể không đủ tài nguyên

nhưng **toàn hệ thống vẫn phải tiếp tục hoạt động** và, khi còn khả năng kỹ thuật hợp lệ, **vẫn phải sản xuất được video** thay vì chuyển ngay sang mô hình `FAILED` toàn phần.
