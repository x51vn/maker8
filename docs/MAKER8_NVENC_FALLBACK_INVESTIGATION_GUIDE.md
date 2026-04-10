# MAKER8 NVENC Fallback Investigation Guide

Tài liệu này dùng cho log dạng:

```json
{
  "event": "normalize.nvenc_fallback",
  "job_id": "8c274bc2-85c5-4410-8bcc-2ba5d8cd69f9",
  "asset_id": "yt_c9iRg6QECkc",
  "returncode": 69,
  "fallback_encoder": "libx264",
  "reason": "nvenc_encode_failed"
}
```

## 1. Log này thực sự có nghĩa gì

Log `normalize.nvenc_fallback` được phát ra từ [`src/maker8/pipeline/normalize.py`](../src/maker8/pipeline/normalize.py) khi:

- `NORMALIZE` chọn `h264_nvenc`
- FFmpeg chạy bằng NVENC bị `CalledProcessError`
- maker8 xóa output dở dang
- maker8 retry lại cùng asset bằng `libx264`

Điểm quan trọng:

- Đây là `warning`, chưa phải failure cuối cùng của job.
- Job chỉ thực sự fail nếu sau đó có `subprocess.failure`, `normalize.asset.skipped`, hoặc stage `NORMALIZE` đi vào DLQ.
- Nếu sau warning này xuất hiện `subprocess.success` với `encoder=libx264` và `normalize.asset.success`, thì hệ thống đã tự cứu bằng CPU fallback.

## 2. Triage nhanh

Tìm theo `job_id` và `asset_id`, rồi phân loại ngay:

```bash
docker logs <container> 2>&1 | rg '8c274bc2-85c5-4410-8bcc-2ba5d8cd69f9|yt_c9iRg6QECkc|normalize\.nvenc_fallback|normalize\.asset\.success|normalize\.asset\.skipped|subprocess\.(success|failure)'
```

Kết luận nhanh:

| Dấu hiệu | Kết luận |
|---|---|
| Có `normalize.nvenc_fallback`, sau đó có `subprocess.success` và `normalize.asset.success` | GPU path lỗi, CPU fallback thành công |
| Có `normalize.nvenc_fallback`, sau đó có `subprocess.failure` hoặc `normalize.asset.skipped` | Asset này không normalize được, cần điều tra tiếp |
| Không có `normalize.nvenc_fallback`, nhưng `NORMALIZE` fail | Đây không phải incident NVENC fallback |

## 3. Luồng code liên quan

Các điểm cần đọc:

- [`src/maker8/pipeline/normalize.py`](../src/maker8/pipeline/normalize.py)
- [`src/maker8/rendering/encoder.py`](../src/maker8/rendering/encoder.py)
- [`src/maker8/rendering/ffmpeg_runtime.py`](../src/maker8/rendering/ffmpeg_runtime.py)
- [`src/maker8/app.py`](../src/maker8/app.py)

Luồng hiện tại:

1. `app.main()` log `app.ffmpeg_runtime` và `app.gpu_capabilities`.
2. `check_nvenc()` chỉ probe xem FFmpeg có expose `h264_nvenc` hay không.
3. `NormalizeStage._normalize_video()` thử encode bằng NVENC.
4. Nếu FFmpeg trả non-zero, maker8 log `normalize.nvenc_fallback`.
5. maker8 retry bằng `_normalize_video_sw()` với `libx264`.

Hàm `_has_video_stream()` quyết định asset `type=video` sẽ đi nhánh video hay audio. Nếu probe stream sai, maker8 có thể cố encode video trên một file audio-only.

## 4. Dấu hiệu quan trọng trong stderr mẫu

Trong stderr bạn gửi có các dấu hiệu sau:

- `major_brand ... encoder ... aac`
- `video:0KiB audio:7365KiB`
- `frame=0`

Đây là tín hiệu rất mạnh cho một trong hai khả năng:

- Input thực tế không có video stream, chỉ có audio.
- Hoặc FFmpeg đã vào nhánh video nhưng không lấy được frame video nào.

Với repo hiện tại, trường hợp đầu đặc biệt đáng nghi nếu asset đến từ YouTube hoặc MP4 chỉ chứa audio.

## 5. Checklist investigation

### 5.1 Xác nhận runtime FFmpeg và GPU

Tìm startup logs:

```bash
docker logs <container> 2>&1 | rg 'app\.ffmpeg_runtime|app\.gpu_capabilities|gpu\.nvenc_probe'
```

Bạn muốn thấy:

- `render_ffmpeg_path` trỏ tới cùng binary mà container thực sự dùng
- `render_nvenc_available=true`
- `nvidia_smi=true`
- `cuda_hwaccel=true`

Nếu `render_nvenc_available=true` nhưng `nvidia_smi=false`, đó là dấu hiệu probe NVENC đang optimistic hơn runtime thực tế.

Kiểm tra thủ công trong container:

```bash
docker exec -it <container> bash -lc '
which ffmpeg ffprobe
ffmpeg -version | head -n 1
ffprobe -version | head -n 1
ffmpeg -hide_banner -encoders | rg h264_nvenc
ffmpeg -hide_banner -hwaccels | rg cuda
nvidia-smi
'
```

### 5.2 Xác nhận file input thật sự có video hay không

Maker8 tạo thư mục job theo pattern:

- `${MAKER8_WORK_DIR:-/data/maker8}/${JOB_ID}/assets`

Ví dụ:

```bash
JOB_ID=8c274bc2-85c5-4410-8bcc-2ba5d8cd69f9
ASSET_DIR=${MAKER8_WORK_DIR:-/data/maker8}/${JOB_ID}/assets
find "$ASSET_DIR" -maxdepth 1 -type f -ls
```

Probe từng file nghi vấn:

```bash
ffprobe -v error -show_entries stream=index,codec_type,codec_name -of json <asset-file>
ffprobe -v error -select_streams v -show_entries stream=index,codec_name -of json <asset-file>
ffprobe -v error -select_streams a -show_entries stream=index,codec_name -of json <asset-file>
ffprobe -v error -show_entries format=filename,duration,size,format_name -of json <asset-file>
```

Kết luận:

- Không có stream `codec_type=video`: asset là audio-only.
- Có video stream nhưng duration/size bất thường: asset có thể corrupt hoặc download dở dang.

### 5.3 Reproduce lệnh normalize ngoài pipeline

Thử đúng command của code path GPU:

```bash
ffmpeg -y \
  -hwaccel cuda \
  -hwaccel_output_format cuda \
  -i <asset-file> \
  -c:v h264_nvenc \
  -preset p4 \
  -cq 23 \
  -c:a aac \
  -b:a 192k \
  -movflags +faststart \
  /tmp/nvenc-test.mp4
```

Nếu job có bật proxy resize thì command thực tế còn thêm `-vf scale=...`; xem [`_build_video_cmd()`](../src/maker8/pipeline/normalize.py).

Thử lại bằng CPU:

```bash
ffmpeg -y \
  -i <asset-file> \
  -c:v libx264 \
  -preset fast \
  -crf 23 \
  -c:a aac \
  -b:a 192k \
  -movflags +faststart \
  /tmp/libx264-test.mp4
```

Diễn giải:

- NVENC fail, CPU pass: lỗi nằm ở GPU path hoặc CUDA decode path.
- Cả hai cùng fail: lỗi nằm ở asset hoặc stream selection, không phải chỉ riêng NVENC.
- Cả hai cùng không tạo được video frame và log `video:0KiB`: gần như chắc input không có video stream hợp lệ.

### 5.4 Xác nhận maker8 resolve đúng `ffprobe`

Repo này từng có điểm yếu: suy ra `ffprobe` bằng string replace từ path `ffmpeg`. Điều đó dễ sai nếu dùng custom binary path.

Kiểm tra runtime đang resolve gì:

```bash
python - <<'PY'
from maker8.rendering.ffmpeg_runtime import resolve_ffmpeg_binary, resolve_ffprobe_binary
print("ffmpeg :", resolve_ffmpeg_binary())
print("ffprobe:", resolve_ffprobe_binary())
PY
```

Nếu `ffprobe` không callable hoặc không match cùng install với `ffmpeg`, logic `_has_video_stream()` có thể cho kết quả sai.

## 6. Root causes thường gặp và cách fix

### Case A. FFmpeg có `h264_nvenc` nhưng container không dùng được GPU

Dấu hiệu:

- `ffmpeg -encoders` có `h264_nvenc`
- nhưng `nvidia-smi` fail hoặc không thấy device
- hoặc stderr chứa các lỗi kiểu `Cannot load libnvidia-encode`, `No NVENC capable devices found`, `Cannot init CUDA`

Fix:

- Chạy container bằng NVIDIA runtime / device plugin đúng cách.
- Đảm bảo container nhìn thấy `/dev/nvidia*`.
- Giữ các env trong image:
  - `NVIDIA_VISIBLE_DEVICES=all`
  - `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`
- Kiểm tra driver trên host tương thích với container runtime.

### Case B. Asset là audio-only nhưng upstream gắn là `video`

Dấu hiệu:

- `ffprobe` không thấy video stream
- stderr có `video:0KiB audio:...`
- asset đến từ YouTube / Google MP4 audio-only

Fix:

- Sửa upstream source selection để lấy progressive video hoặc stream có video thật.
- Nếu asset thực chất chỉ là audio, truyền `asset.type=audio` hoặc xử lý nó ở nhánh audio.
- Theo dõi log `normalize.no_video_stream`; đó mới là path đúng cho audio-only.

### Case C. `ffprobe` resolve sai hoặc probe stream fail

Dấu hiệu:

- `resolve_ffmpeg_binary()` trả về custom path lạ
- `ffprobe` cùng runtime không tồn tại
- `_has_video_stream()` không phát hiện audio-only và cứ đi nhánh video

Fix:

- Dùng resolver `ffprobe` chuẩn, không suy luận bằng string replace.
- Đảm bảo `ffprobe` là sibling của FFmpeg đang dùng hoặc có mặt trên `PATH`.
- Repo này đã được harden theo hướng đó trong [`src/maker8/rendering/ffmpeg_runtime.py`](../src/maker8/rendering/ffmpeg_runtime.py).

### Case D. Asset corrupt hoặc download dở dang

Dấu hiệu:

- `ffprobe` fail đọc duration hoặc stream
- file size bất thường
- có incident trước đó về partial artifact

Fix:

- Xóa artifact lỗi trong thư mục job rồi chạy lại.
- Kiểm tra `DOWNLOAD` logs và disk pressure.
- Xác nhận không có `SIGKILL`, `disk full`, `OOM`, hoặc volume issue.

### Case E. GPU encode dùng được nhưng `-hwaccel cuda` decode bị fail với source cụ thể

Dấu hiệu:

- Encode NVENC cho synthetic test chạy được
- Nhưng command normalize thật với `-hwaccel cuda -i <asset>` fail
- CPU fallback lại thành công

Fix:

- Đây là lỗi ở decode path hoặc source codec/pixel format cụ thể.
- Cách fix thực dụng nhất là thêm một nhánh retry trung gian:
  - CPU decode + NVENC encode
  - nếu vẫn fail mới rơi xuống `libx264`

Repo hiện chưa có nhánh retry trung gian này.

## 7. Khuyến nghị fix ở mức code

Nếu incident này lặp lại nhiều, thứ tự hardening hợp lý là:

1. Giữ `ffmpeg` và `ffprobe` cùng runtime, không dùng string replace path.
2. Log rõ stderr reason theo nhóm: `gpu_unavailable`, `audio_only_input`, `corrupt_input`, `cuda_decode_failed`.
3. Nâng `check_nvenc()` từ probe danh sách encoder sang smoke test thực sự.
4. Thêm retry trung gian: CPU decode + `h264_nvenc`, rồi mới fallback `libx264`.
5. Log metadata stream của asset ngay trước normalize để operator không phải tự probe lại từ đầu.

## 8. Definition of done sau khi fix

Một fix được xem là đúng khi:

- Asset audio-only không còn đi vào video normalize.
- Asset video hợp lệ trên máy có GPU không còn log `normalize.nvenc_fallback` hàng loạt.
- Startup logs cho thấy runtime FFmpeg/NVENC/CUDA nhất quán.
- Nếu GPU path fail thật, CPU fallback vẫn tạo được artifact hợp lệ và không phát sinh false-positive investigation.

## 9. Thay đổi đã áp dụng trong repo

Để giảm khả năng mis-detect stream trong incident kiểu này, repo hiện đã đổi `NORMALIZE` sang resolve `ffprobe` bằng resolver riêng thay vì `replace("ffmpeg", "ffprobe")`.

Các file liên quan:

- [`src/maker8/rendering/ffmpeg_runtime.py`](../src/maker8/rendering/ffmpeg_runtime.py)
- [`src/maker8/pipeline/normalize.py`](../src/maker8/pipeline/normalize.py)
- [`tests/test_ffmpeg_runtime.py`](../tests/test_ffmpeg_runtime.py)
