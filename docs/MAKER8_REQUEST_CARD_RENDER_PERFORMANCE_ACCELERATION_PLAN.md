# Maker8 Request Card: Render Performance Acceleration Plan

## 1. Summary

`maker8` hiện render quá chậm ở runtime thực tế.

Quan sát thực tế:

- render chỉ đạt khoảng `3.03 frame/s`
- một video khoảng `60s` với gần `1800` frame có thể mất khoảng `10 phút` để hoàn thành render
- hệ thống có GPU NVIDIA nhưng hiệu năng tổng thể vẫn thấp

Vấn đề này không thể giải quyết bằng một thay đổi nhỏ ở encoder.

Root cause là tổng hợp của:

- split-brain FFmpeg runtime giữa `NORMALIZE` và `RENDER`
- GPU chỉ giúp encode, trong khi phần lớn render path vẫn CPU/Python-bound
- source media không được proxy/downscale sớm
- một số effect đang là Python per-frame
- kiến trúc MoviePy-first khiến throughput thấp khi số frame/layer/effect tăng

Yêu cầu của card này là nâng hiệu năng `maker8` theo hướng có cấu trúc, đo được, và bền vững.

## 2. Problem Statement

Hiện tại `maker8` đang có đặc điểm:

- encode có thể dùng GPU trong một số điều kiện
- nhưng scene composition vẫn chủ yếu nằm trong Python/MoviePy
- resize/crop/effect được thực hiện muộn, theo từng frame
- một số effect chạy bằng callback Python thay vì FFmpeg filter graph

Điều đó dẫn đến:

- GPU utilization không chuyển hóa thành render throughput tương xứng
- CPU trở thành bottleneck chính
- video càng dài, nhiều scene, nhiều effect, nhiều layer thì tốc độ càng sụt
- hệ thống khó scale throughput theo kỳ vọng

## 3. Observed Root Causes

### 3.1 Split FFmpeg runtime

`NORMALIZE` và `RENDER` hiện không dùng cùng một FFmpeg binary.

Hệ quả:

- GPU probe không phản ánh đúng render runtime thực tế
- render path có thể fail rồi fallback CPU
- hệ thống mất hiệu năng trước cả khi nói tới composition

### 3.2 GPU only accelerates a subset of the pipeline

Ngay cả khi GPU render path hoạt động:

- MoviePy vẫn phải build clip graph
- đọc asset
- crop/resize/rotate/composite
- mix audio
- apply effect logic
- generate từng frame trong Python

Nên “GPU enabled” không đồng nghĩa “pipeline nhanh”.

### 3.3 Resize/crop happens too late

Source asset sau `NORMALIZE` vẫn có thể lớn hơn nhiều so với canvas hoặc layer rect.

Khi đó:

- render path vẫn phải resize/crop mỗi frame
- CPU và memory bandwidth bị đốt ở layer stage thay vì một lần ở preprocess

### 3.4 Per-frame effects are too expensive

Một số effect hiện được implement bằng Python callback per-frame, ví dụ kiểu `zoom_pan`.

Đây là một trong những bottleneck nặng nhất.

Mỗi frame:

- lấy frame từ clip
- crop
- resize bằng Pillow
- convert numpy array

Cách làm này không phù hợp cho production renderer cần throughput cao.

### 3.5 Architecture is still MoviePy-first

Hiện tại:

- scene composition được build trong Python
- encode chỉ là bước cuối

Khi scene graph phức tạp:

- Python overhead tăng mạnh
- GIL / callback / numpy / Pillow cost chiếm ưu thế

## 4. Goals

Mục tiêu của effort này:

- tăng render throughput đáng kể trên cùng hardware
- giảm độ lệ thuộc vào Python per-frame processing
- tận dụng GPU đúng chỗ và nhất quán
- tạo pipeline dễ profile, dễ tối ưu tiếp, và dễ maintain

Không chỉ cần “nhanh hơn”.
Hệ thống phải:

- predictable hơn
- deterministic hơn
- ít fallback CPU không chủ đích hơn

## 5. Required Workstreams

## 5.1 Workstream A: Unify FFmpeg runtime

Đây là prerequisite bắt buộc.

Yêu cầu:

- `NORMALIZE` và `RENDER` phải dùng cùng một FFmpeg runtime
- MoviePy/ImageIO phải bị ép dùng FFmpeg hệ thống chuẩn
- probe capability phải chạy trên đúng binary thực tế

Deliverables:

- remove split-brain FFmpeg behavior
- render binary path được log rõ
- startup mismatch detection
- GPU capability log phản ánh đúng render path

## 5.2 Workstream B: Move expensive transforms earlier

Các transform nặng phải được đẩy về preprocess thay vì làm lại trên từng frame.

Yêu cầu:

- scale source video về target proxy size sớm trong `NORMALIZE`
- tạo proxy assets theo target canvas / target scene profile
- giảm lượng dữ liệu mà render path phải xử lý per-frame

Ví dụ:

- video source 4K nhưng output 1080x1920 thì phải downscale sớm
- layer chỉ dùng vùng nhỏ thì nên cân nhắc pre-crop / pre-scale

Deliverables:

- proxy generation policy
- per-asset target resolution strategy
- normalized/proxy asset reuse

## 5.3 Workstream C: Implement lại các effect per-frame bằng FFmpeg filter

Đây là yêu cầu bắt buộc.

Các effect per-frame hiện tại phải được re-implement bằng FFmpeg filter graph thay vì callback Python.

Phạm vi tối thiểu:

- `zoom_pan`
- các effect tương tự đang làm crop/resize/transform từng frame trong Python

Yêu cầu chi tiết:

- dùng FFmpeg filters như `zoompan`, `scale`, `crop`, `fade`, `overlay`, `rotate`, `eq`, hoặc filter graph tương đương
- nếu effect nào chưa thể migrate hoàn toàn ngay, phải có plan phased migration
- effect Python per-frame không được giữ làm default path cho production render

Expected benefits:

- giảm mạnh Python overhead
- tận dụng native media pipeline tốt hơn
- dễ scale hiệu năng hơn

Deliverables:

- inventory toàn bộ effect nào đang per-frame
- mapping effect -> FFmpeg filter strategy
- implementation mới cho các effect nặng
- deprecate hoặc disable default path cũ

## 5.4 Workstream D: Re-architect render execution path

Pipeline render không nên giữ mãi mô hình “compose toàn bộ video trong Python rồi encode cuối”.

Yêu cầu:

- xem xét pre-render từng scene thành intermediate MP4
- concatenate scenes bằng FFmpeg thay vì giữ graph quá lớn trong MoviePy
- cô lập effect-heavy scene khỏi phần còn lại của video

Ưu tiên refactor:

1. render scene independently
2. concat scene outputs
3. carry audio/transition policy rõ ràng

Lợi ích:

- scene-level profiling rõ hơn
- retry/failure isolation tốt hơn
- memory footprint thấp hơn
- render graph ít phình to hơn

## 5.5 Workstream E: Add performance operating modes

Hệ thống cần mode vận hành rõ ràng:

- `quality`
- `balanced`
- `fast`

Mỗi mode có thể điều chỉnh:

- fps
- bitrate
- effect allowance
- proxy resolution
- transition complexity

Điều này giúp operator có tradeoff thực tế thay vì chỉ một path cố định.

## 5.6 Workstream F: Add profiling and bottleneck visibility

Không thể tối ưu bền vững nếu không có profiling chuẩn.

Yêu cầu:

- log per scene:
  - scene render time
  - source resolutions
  - number of layers
  - effect list
- log per asset:
  - proxy size
  - normalize strategy
  - reused vs transformed
- log final:
  - total frames
  - average fps
  - CPU/GPU path actually used

Nếu không có data này, các tối ưu tiếp theo sẽ lại là phỏng đoán.

## 6. Best Practices That Must Be Applied

## 6.1 Encode acceleration is not enough

Không được đánh đồng `NVENC available` với `render optimized`.

Phải tối ưu toàn pipeline:

- decode
- transform
- compose
- encode

## 6.2 Avoid Python per-frame processing in production paths

Python callback per-frame chỉ nên:

- dùng cho prototype
- hoặc fallback hiếm

Không được là đường chạy chính cho production rendering.

## 6.3 Preprocess once, reuse many times

Resize/crop/proxy hóa asset một lần ở preprocess tốt hơn rất nhiều so với làm lại trên từng frame.

## 6.4 Profile before and after every major change

Mỗi optimization phải có benchmark:

- input representative
- baseline
- after-change measurement
- resource usage comparison

## 6.5 Keep render runtime deterministic

FFmpeg runtime, encoder config, effect execution path, proxy policy phải deterministic giữa:

- local
- CI
- staging
- production

## 6.6 Prefer native media pipeline over Python graph when possible

Khi một transform có thể làm tốt hơn bằng FFmpeg filter, không nên giữ Python implementation trên đường nóng.

## 7. Required Deliverables

## 7.1 Architecture deliverables

- tài liệu hóa render hot path
- decision record cho:
  - unified FFmpeg runtime
  - proxy strategy
  - FFmpeg filter migration
  - scene-level render strategy

## 7.2 Code deliverables

- unify FFmpeg runtime
- proxy/downscale strategy trong normalize path
- migrate effect per-frame sang FFmpeg filters
- render path refactor theo scene/intermediate outputs nếu cần
- performance mode configuration
- profiling logs

## 7.3 Test deliverables

Phải có tests cho:

- render uses same FFmpeg runtime as normalize
- GPU path actually active on render runtime
- per-frame effect migration preserves behavior acceptably
- large source video is proxied/downscaled before hot render path
- render throughput regression checks trên representative fixture set

## 7.4 Benchmark deliverables

Phải có benchmark trước/sau trên ít nhất:

- video ngắn ít effect
- video trung bình có image + text + narration
- video nhiều scene có motion/effect
- video với source resolution lớn hơn output resolution nhiều lần

Metrics tối thiểu:

- frames/sec
- wall-clock render time
- CPU utilization
- GPU utilization
- memory usage

## 8. Suggested Implementation Order

1. Unify FFmpeg runtime giữa normalize và render.
2. Thêm profiling logs và benchmark baseline.
3. Proxy/downscale source assets sớm ở normalize/preprocess.
4. Implement lại các effect per-frame bằng FFmpeg filter.
5. Refactor render path theo scene/intermediate outputs nếu bottleneck vẫn lớn.
6. Thêm performance modes và policy controls.

## 9. Definition Of Done

Chỉ coi là hoàn thành khi:

- render runtime không còn split-brain với normalize runtime
- không còn effect production-critical nào chạy bằng Python per-frame path
- source assets lớn được proxy/downscale trước khi đi vào hot composition path
- benchmark cho thấy throughput tăng rõ rệt trên representative workloads
- operator có profiling data đủ để biết bottleneck còn lại nằm ở đâu

## 10. Final Requirement

Vấn đề hiệu năng hiện tại không được xử lý bằng các fix cục bộ rời rạc.

Yêu cầu cuối cùng là:

> `maker8` phải được refactor từ một pipeline “MoviePy-first, Python-heavy” sang một pipeline tận dụng FFmpeg/native media execution nhiều hơn, với GPU được dùng đúng chỗ, runtime được thống nhất, và các effect per-frame được thay bằng FFmpeg filter trên đường chạy production.
