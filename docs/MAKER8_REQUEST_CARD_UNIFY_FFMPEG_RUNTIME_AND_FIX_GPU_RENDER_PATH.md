# Maker8 Request Card: Unify FFmpeg Runtime And Fix GPU Render Path

## 1. Summary

`maker8` hiện có một lỗi kiến trúc quan trọng trong media runtime:

- `NORMALIZE` và `RENDER` không dùng cùng một FFmpeg binary
- startup GPU probe đang kiểm tra FFmpeg hệ thống
- nhưng MoviePy render path lại dùng FFmpeg bundled của `imageio_ffmpeg`
- hai binary này có capability khác nhau

Kết quả:

- `NORMALIZE` có thể dùng `h264_nvenc` thành công
- `RENDER` vẫn fail ở GPU encode
- hệ thống kết luận “GPU available” nhưng render runtime thực tế lại không support NVENC

Đây là lỗi **split-brain FFmpeg runtime** và phải được fix triệt để.

## 2. Incident Evidence

### 2.1 Runtime logs

Job log đã cho thấy:

- GPU render path được chọn
- MoviePy/FFmpeg fail với:

```text
Broken pipe
MoviePy error: FFMPEG encountered the following error while writing file ...
Unrecognized option 'cq'.
Error splitting the argument list: Option not found
```

sau đó `maker8` fallback sang CPU encode.

### 2.2 Container verification

Các lệnh xác minh trong container cho thấy:

#### FFmpeg hệ thống

```bash
docker exec maker8-render-worker ffmpeg -hide_banner -h encoder=h264_nvenc
```

Kết quả:

- nhận diện được `h264_nvenc`
- có option `-cq`

#### FFmpeg mà MoviePy đang dùng

```bash
docker exec maker8-render-worker python3 -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'
docker exec maker8-render-worker python3 -c 'from moviepy.config import FFMPEG_BINARY; print(FFMPEG_BINARY)'
```

Kết quả:

```text
/usr/local/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2
```

#### Capability của binary bundled này

```bash
docker exec maker8-render-worker /usr/local/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2 -hide_banner -h encoder=h264_nvenc
```

Kết quả:

```text
Codec 'h264_nvenc' is not recognized by FFmpeg.
```

## 3. Root Cause

Root cause không phải là:

- GPU máy bị lỗi
- NVIDIA runtime không hoạt động
- FFmpeg hệ thống không có NVENC

Root cause là:

### 3.1 Normalize path uses system FFmpeg

`NORMALIZE` gọi trực tiếp binary `ffmpeg` hệ thống.

Binary này có:

- `h264_nvenc`
- `-cq`
- GPU encode support

### 3.2 Render path uses bundled FFmpeg from imageio_ffmpeg

`RENDER` đi qua `MoviePy.write_videofile()`, và MoviePy đang dùng binary bundled của `imageio_ffmpeg`.

Binary này:

- không nhận `h264_nvenc`
- không support option set mà render path đang dùng cho GPU

### 3.3 Startup probe checks the wrong runtime for render

App startup đang probe GPU capability theo FFmpeg hệ thống.

Điều đó tạo false confidence:

- probe báo GPU render available
- nhưng binary thực sự dùng ở render path lại không support NVENC

Nói cách khác: **capability probe và execution runtime không cùng một binary**.

## 4. Problem Statement

`maker8` hiện có split media toolchain:

- một toolchain cho normalize
- một toolchain khác cho render

Điều này là anti-pattern rất nghiêm trọng với media pipeline.

Hệ quả:

- capability check không còn đáng tin
- log “GPU enabled” không còn phản ánh behavior thực
- cùng một job có thể dùng GPU ở stage này và fail vô lý ở stage khác
- incident trở nên khó debug
- fallback CPU bị dùng nhiều hơn cần thiết
- performance, correctness, reproducibility đều bị ảnh hưởng

## 5. Required End State

Hệ thống phải đạt trạng thái sau:

### 5.1 Single source of truth for FFmpeg runtime

Toàn bộ `maker8` phải dùng **một** FFmpeg runtime thống nhất cho:

- probe
- normalize
- render
- validation / smoke checks

### 5.2 Render path must use the same FFmpeg binary as normalize

MoviePy không được tự ý dùng bundled FFmpeg khác với binary hệ thống đã được chuẩn hóa.

### 5.3 Startup checks must validate the actual render binary

Startup probe phải xác minh đúng binary mà `MoviePy.write_videofile()` sẽ dùng.

### 5.4 GPU claims must reflect real executable capability

Chỉ được log `gpu_render_enabled=true` nếu binary render path thực tế support encoder/options cần thiết.

## 6. Required Changes

## 6.1 Force MoviePy/ImageIO to use system FFmpeg

Phải cấu hình render stack dùng FFmpeg hệ thống, ví dụ:

- set `IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg`
- hoặc set `FFMPEG_BINARY=/usr/bin/ffmpeg`
- hoặc cấu hình tương đương được MoviePy/ImageIO thực sự honor

Yêu cầu:

- config này phải được áp ngay trong image/container runtime
- không phụ thuộc vào việc developer local có env var thủ công hay không

## 6.2 Remove split-brain behavior from startup

Startup phải log rõ:

- system ffmpeg path
- render ffmpeg path thực tế
- two paths có giống nhau không
- render ffmpeg version
- render ffmpeg encoder support (`h264_nvenc`)

Nếu render binary không đạt capability yêu cầu, startup phải:

- log rõ mismatch
- tắt GPU render path
- hoặc fail-fast nếu deployment policy yêu cầu GPU render

## 6.3 Probe the actual render binary, not a generic `ffmpeg`

Refactor `probe_gpu_capabilities()` và các helper liên quan để:

- nhận path FFmpeg cụ thể
- probe đúng executable được dùng ở render path
- không chỉ probe `ffmpeg` trên PATH

## 6.4 Add runtime guard before GPU encode

Trước khi dùng GPU encode config ở `compose_video()`:

- verify binary thực tế support codec và options required
- verify `h264_nvenc` tồn tại
- verify chosen quality parameters hợp lệ với binary đó

Nếu không hợp lệ:

- đừng vào GPU path
- log reason cụ thể
- chọn fallback có chủ đích

## 6.5 Unify FFmpeg configuration across pipeline stages

Mọi nơi dùng FFmpeg phải đi qua một abstraction chung, ví dụ:

- `resolve_ffmpeg_binary()`
- `MediaRuntimeConfig`
- `FFmpegRuntime`

Không được:

- stage này hardcode `ffmpeg`
- stage khác implicit dùng MoviePy default
- probe chạy ở binary A, execute chạy ở binary B

## 6.6 Make encoder config runtime-aware

`EncoderConfig` hiện assume binary support `-cq` nếu NVENC available.

Yêu cầu:

- encoder config phải phụ thuộc vào binary capability thực tế
- option set phải được derive từ executable thật
- nếu binary không support requested codec/options thì phải fail-fast hoặc fallback rõ ràng

## 6.7 Add deployment-level enforcement

Container/build/deploy phải đảm bảo:

- system FFmpeg là binary chuẩn được hỗ trợ
- render runtime thực tế dùng binary đó
- không để image kéo theo bundled FFmpeg ngoài ý muốn

Nếu cần:

- set env ngay trong Dockerfile
- set env ở entrypoint
- thêm startup assertion trong container health/init

## 7. Best Practices That Must Be Applied

## 7.1 One media pipeline, one binary runtime

Một pipeline media production không được dùng nhiều FFmpeg runtime khác nhau nếu không có chủ đích cực kỳ rõ ràng.

## 7.2 Probe what you execute

Không probe binary A rồi chạy binary B.

Mọi capability check phải gắn với executable thật sự sẽ được dùng.

## 7.3 Do not rely on library defaults for critical infra behavior

`MoviePy`/`imageio_ffmpeg` default binary resolution không được xem là đủ an toàn cho production.

FFmpeg path phải được control explicit.

## 7.4 Fail fast on capability mismatch

Nếu render binary không support GPU encode, phải biết điều đó ở startup hoặc preflight, không đợi tới lúc render frame đầu tiên.

## 7.5 Keep toolchain deterministic across environments

Local, CI, staging, production phải dùng cùng strategy resolve FFmpeg binary.

## 8. Required Deliverables

## 8.1 Code deliverables

- abstraction chung để resolve FFmpeg binary
- render path ép dùng system FFmpeg
- probe logic dùng đúng render binary
- startup logs/guards cho FFmpeg mismatch
- runtime fallback logic rõ ràng khi GPU render unavailable

## 8.2 Deployment deliverables

- Dockerfile / entrypoint / env updates để bind MoviePy vào system FFmpeg
- deployment docs cập nhật
- startup verification step trong runtime

## 8.3 Test deliverables

Phải có tests cho:

- render ffmpeg path == system ffmpeg path
- render probe detects NVENC support on actual binary
- capability mismatch disables GPU render path
- no split-brain between normalize and render FFmpeg runtime

## 8.4 Observability deliverables

Phải log được:

- `system_ffmpeg_path`
- `render_ffmpeg_path`
- `same_binary`
- `render_nvenc_available`
- chosen codec/preset/path per render job

## 9. Definition Of Done

Chỉ coi là hoàn thành khi:

- `NORMALIZE` và `RENDER` dùng cùng một FFmpeg runtime
- startup probe phản ánh đúng binary thực tế của render path
- render GPU path không còn fail vì capability mismatch giữa 2 binary
- logs cho thấy render binary path và capability rõ ràng
- CI có regression test ngăn split-brain FFmpeg runtime tái xuất hiện

## 10. Final Requirement

Vấn đề này phải được xử lý như một lỗi kiến trúc runtime, không phải chỉ là lỗi option `-cq`.

Không được “fix” bằng cách:

- bỏ tạm `-cq`
- tắt GPU render hoàn toàn mà không giải quyết split runtime
- giữ nguyên MoviePy default binary resolution

Yêu cầu cuối cùng là:

> `maker8` phải dùng một FFmpeg runtime thống nhất, có capability được probe đúng, và mọi GPU render decision phải dựa trên executable thực tế đang chạy.
