# Maker8 Request Changes Card: Fix Asset-Level NVENC Normalize Failure And Improve Observability

## 1. Summary

Request changes.

Log incident này không cho thấy `maker8` “mất GPU” ở mức toàn hệ thống.

Ngược lại, log cho thấy:

- các asset trước đó vẫn normalize thành công bằng `h264_nvenc`
- asset `yt_TYxc5fhZgr8` bắt đầu bằng path `h264_nvenc`
- FFmpeg sau đó fail với `returncode=69`
- pipeline fallback sang `libx264` đúng như code hiện tại

Vấn đề cần sửa không phải là “thêm fallback”.

Vấn đề cần sửa là:

- quyết định GPU/CPU hiện còn quá thô ở mức process-wide
- preflight cho asset video hiện quá yếu
- logging hiện không giữ lại dòng lỗi FFmpeg quan trọng nhất
- operator chưa thể phân biệt rõ:
  - asset không có video decode được
  - asset có video stream nhưng GPU path không xử lý được
  - asset fail ở decode, encode init, hay muxing

## 2. Incident Evidence

Các tín hiệu quan trọng từ log được cung cấp:

- `2026-03-22T09:54:08Z`: các asset `yt_a9ypomrKNiw` và `yt_0nCkSzgScZE` normalize thành công với `encoder="h264_nvenc"`
- `2026-03-22T09:54:11Z`: asset `yt_TYxc5fhZgr8` bắt đầu `normalize_video` với `encoder="h264_nvenc"`
- `2026-03-22T09:59:01Z`: cùng asset này phát ra `normalize.nvenc_fallback`
- warning đó có:
  - `returncode=69`
  - `reason="nvenc_encode_failed"`
  - stderr tail cho thấy:
    - `video:0KiB`
    - `audio:132294KiB`
    - `frame=0`
    - `Conversion failed!`
- ngay sau đó pipeline bắt đầu lại bằng `encoder="libx264"`

Diễn giải:

- đây không phải external kill như `SIGKILL`
- đây là FFmpeg tự fail và trả về mã lỗi dương
- audio đã được xử lý, nhưng video path không sinh ra frame hợp lệ
- lỗi có tính asset-specific nhiều hơn là system-wide, vì cùng job đã có asset khác dùng `h264_nvenc` thành công

Lưu ý:

- đoạn log được cung cấp dừng ở thời điểm CPU fallback bắt đầu
- chưa có bằng chứng trong snippet này để kết luận asset cuối cùng normalize thành công hay tiếp tục fail ở CPU path

## 3. Why Current Behavior Is Not Good Enough

Hiện tại logic chọn GPU trong `NORMALIZE` phụ thuộc vào `check_nvenc()` là probe process-wide được cache một lần ở `src/maker8/rendering/encoder.py`.

Sau đó `NormalizeStage._normalize_video()` dùng kết quả đó để chọn `h264_nvenc` hay `libx264` cho asset hiện tại trong `src/maker8/pipeline/normalize.py`.

Điểm yếu ở đây:

### 3.1 Process-level GPU availability != per-asset GPU suitability

`check_nvenc()` chỉ trả lời câu hỏi:

- runtime này có expose `h264_nvenc` hay không

Nó không trả lời:

- asset cụ thể này có decode được trên GPU hay không
- stream video có thực sự sinh frame được hay không
- codec / pix_fmt / container của asset có phù hợp với CUDA path hay không

### 3.2 `_has_video_stream()` đang quá nhẹ

`_has_video_stream()` chỉ kiểm tra có xuất hiện stream `video` trong metadata hay không.

Nó không kiểm tra:

- stream đó có decode được không
- có frame thực hay không
- ffprobe có đang chỉ thấy “nominal video stream” nhưng nội dung thực tế corrupt / unsupported hay không

Điều này giải thích vì sao một asset vẫn đi vào `_normalize_video()` nhưng cuối cùng FFmpeg lại kết thúc với:

- `frame=0`
- `video:0KiB`

### 3.3 Stderr bị cắt theo tail làm mất nguyên nhân thật

`truncate_stderr()` hiện chỉ giữ 500 ký tự cuối.

Với FFmpeg, dòng nguyên nhân quan trọng thường nằm trước phần thống kê cuối cùng, ví dụ:

- lỗi init encoder
- lỗi hwaccel decode
- lỗi filter graph
- lỗi opening output stream

Kết quả là log hiện chỉ còn:

- `video:0KiB`
- `audio:132294KiB`
- `Conversion failed!`

Điều đó chưa đủ để chẩn đoán chính xác root cause.

## 4. Required Changes

## 4.1 Add a real per-asset media probe before choosing normalize strategy

Trước khi chọn `h264_nvenc`, phải probe asset ở mức chi tiết hơn, ít nhất gồm:

- số lượng video/audio streams
- `codec_name`
- `pix_fmt`
- width / height
- frame rate
- duration
- có frame video decode được hay không

Không được coi `nvenc_available=true` ở process-level là đủ để route mọi asset video vào GPU path.

## 4.2 Replace binary GPU/CPU selection with strategy selection

Thay vì:

- `NVENC available -> dùng GPU`
- `không có NVENC -> dùng CPU`

phải chọn strategy theo từng asset, ví dụ:

- CPU decode + GPU encode
- full CPU
- full GPU
- audio-only normalize
- fail-fast nếu asset khai báo `video` nhưng không có video decode được

Mục tiêu là tránh attempt GPU sai loại rồi chỉ biết fallback mù.

## 4.3 Distinguish “no decodable video” from “GPU path failed on a valid video asset”

Hiện tại operator chưa biết asset này thuộc case nào:

- asset audio-only nhưng metadata/contract gắn nhầm là `video`
- asset có video stream danh nghĩa nhưng stream hỏng
- asset có video hợp lệ nhưng CUDA/NVENC path fail

Phải log và classify rõ các trường hợp này thành các reason code riêng.

Ví dụ:

- `no_video_stream`
- `no_decodable_video_frames`
- `gpu_decode_failed`
- `gpu_encode_init_failed`
- `gpu_zero_frame_output`
- `cpu_fallback_started`
- `cpu_fallback_succeeded`
- `cpu_fallback_failed`

## 4.4 Improve FFmpeg error capture

Không được chỉ log tail 500 ký tự của stderr cho case FFmpeg failure.

Cần bổ sung ít nhất một trong các cách sau:

- trích ra dòng lỗi đầu tiên có tín hiệu mạnh
- log cả “first meaningful error line” và “stderr tail”
- lưu stderr đầy đủ vào artifact debug theo `job_id` / `asset_id`

Mục tiêu là để operator không phải đoán từ `Conversion failed!`.

## 4.5 Validate output semantics, not just file existence

Sau mỗi lần normalize video thành công, cần verify output có:

- ít nhất một video stream đọc được
- duration hợp lệ
- video frame count hoặc metadata video nhất quán

Không nên coi một file MP4 có duration nhưng không có video hữu ích là “normalize success”.

## 4.6 Add tests for this exact failure class

Phải có test bao phủ các case sau:

- asset type là `video` nhưng thực tế không có video stream
- asset có video stream danh nghĩa nhưng không decode ra frame
- NVENC path fail trên một asset nhưng asset khác trong cùng job vẫn dùng NVENC thành công
- CPU fallback được log rõ là started / succeeded / failed
- log failure giữ lại đủ error context để phân loại nguyên nhân

## 5. Acceptance Criteria

Thay đổi này chỉ được coi là hoàn tất khi:

1. cùng một incident class như `yt_TYxc5fhZgr8` không còn chỉ cho ra một log mơ hồ kiểu `video:0KiB ... Conversion failed!`
2. operator có thể nhìn log và biết asset fail ở:
   - stream probe
   - GPU decode
   - GPU encode
   - zero-frame output
   - hay CPU fallback
3. khi GPU path fail trên một asset, log phải nói rõ CPU fallback có chạy hay không và outcome của fallback đó
4. asset không có video decode được phải được phân loại rõ, không đi qua path “trông như video bình thường”
5. test suite có case regression cho asset-level NVENC failure

## 6. Bottom Line

Lỗi hiện tại không phải là “GPU không hoạt động”.

Lỗi hiện tại là:

- hệ thống mới biết GPU có tồn tại
- nhưng chưa biết asset nào phù hợp với GPU path
- và khi asset-specific NVENC failure xảy ra thì log chưa đủ tốt để giải thích chính xác

Request changes cho đến khi `maker8` có:

- asset-level strategy selection
- media probe đủ mạnh
- failure classification rõ
- observability đủ để chẩn đoán FFmpeg/NVENC incidents mà không phải suy đoán từ stderr tail
