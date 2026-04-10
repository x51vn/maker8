# Uploader `channel_id` Investigation And Contract Cleanup Guide

## 1. Purpose

Tài liệu này có 2 mục tiêu:

1. điều tra vì sao `uploader_metadata.channel_id` đang có thể bị rỗng
2. đưa ra phương hướng fix theo hướng kiến trúc `solid but simple`, đồng thời dọn toàn bộ drift interface, legacy code, dead code, code redundant và logic phức tạp không cần thiết quanh uploader contract

Đây là guide điều tra và remediation. Đây không phải implementation plan chi tiết theo task breakdown.

## 2. Project Context

`maker8` là render worker trong hệ thống `editor8 -> maker8`.

Luồng hiện tại:

```text
editor8
  -> Kafka: video.render.request.v1
    -> maker8
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

Điểm boundary quan trọng:

- `editor8` là upstream producer và nơi hợp lý để build request đúng contract
- `maker8` là downstream render worker, không phải UI/control plane
- repo hiện tại là repo `maker8`, không chứa source code của `editor8`

Vì vậy, các kết luận về upstream được nêu trong tài liệu này là kết luận suy ra từ:

- shared contract trong repo hiện tại
- examples, fixtures, schema, tests
- runtime behavior của `maker8`

## 3. Symptom

User đã cung cấp payload với:

```json
"uploader_metadata": {
  "channel_id": "",
  ...
}
```

Đây là tín hiệu cho thấy metadata uploader đang không được materialize đầy đủ trước khi request được gửi hoặc trước khi payload được dùng cho downstream publish flow.

## 4. Verified Findings From Current Codebase

### 4.1 `channel_id` hiện được phép rỗng ngay từ canonical contract

Canonical model ở [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) định nghĩa:

- `UploaderMetadata.channel_id: str = ""`

Điều này có nghĩa:

- field tồn tại trong contract
- nhưng không có invariant non-empty ở model level
- request hoàn toàn có thể parse thành công với `channel_id = ""`

### 4.2 `maker8` không tự xóa hoặc biến đổi `channel_id`

Theo current runtime:

- orchestrator copy `request.uploader_metadata` vào `PipelineContext` trong [`src/maker8/pipeline/orchestrator.py`](../src/maker8/pipeline/orchestrator.py)
- `PipelineContext` chỉ giữ object đó, không mutate trong [`src/maker8/pipeline/context.py`](../src/maker8/pipeline/context.py)
- `EMIT_RESULT` forward nguyên `ctx.uploader_metadata` vào `RenderResult` trong [`src/maker8/pipeline/emit.py`](../src/maker8/pipeline/emit.py)
- `UPLOAD_DROPBOX` forward nguyên `ctx.uploader_metadata` vào manifest trong [`src/maker8/pipeline/upload.py`](../src/maker8/pipeline/upload.py)

Kết luận đã xác minh:

- `maker8` không phải nơi làm mất `channel_id`
- nếu `channel_id` rỗng ở output, khả năng cao nó đã rỗng từ input

### 4.3 `VALIDATE` không enforce rule nào cho `uploader_metadata.channel_id`

`ValidateStage` trong [`src/maker8/pipeline/validate.py`](../src/maker8/pipeline/validate.py) hiện validate:

- spec version
- scene count
- asset refs
- V2 planning
- V2 subtitle
- V2 layer role / missing asset policy

Nhưng không validate:

- `uploader_metadata.channel_id`
- consistency giữa `uploader_metadata.channel_id` và `publish.targets[].account_ref`
- publish-ready semantics nào liên quan tới uploader metadata

### 4.4 Examples và fixtures đang drift về chính identity này

Current repo có drift rõ ràng:

- [`docs/examples/render_request.example.json`](../docs/examples/render_request.example.json) có `uploader_metadata.channel_id = "yt:my-channel-123"`
- [`docs/examples/render_request_v2.example.json`](../docs/examples/render_request_v2.example.json) không có `channel_id`
- [`docs/examples/render_request_minimal.example.json`](../docs/examples/render_request_minimal.example.json) dùng `uploader_metadata: {}`
- [`tests/fixtures/golden_editor8_full_request.json`](../tests/fixtures/golden_editor8_full_request.json) dùng `channel_id = "yt:test-channel"` nhưng `publish.targets[0].account_ref = "channel:test-channel"`
- [`docs/examples/render_result_success.example.json`](../docs/examples/render_result_success.example.json) cũng có lệch tương tự giữa `channel_id` và `account_ref`

Drift này cho thấy repo hiện chưa có canonical format rõ ràng cho publisher identity.

### 4.5 Identity taxonomy đang không nhất quán

Hiện tại có ít nhất 3 kiểu format khác nhau xuất hiện trong repo:

- `yt:test-channel`
- `channel:test-channel`
- `yt-channel-main`

Điều này làm cho các câu hỏi cơ bản trở nên mơ hồ:

- field nào mới là source of truth cho account identity
- prefix nào mới là canonical
- downstream phải match theo field nào

### 4.6 Có duplication về nghĩa giữa `channel_id` và `account_ref`

`uploader_metadata.channel_id` và `publish.targets[].account_ref` đều đang mang nghĩa gần với publisher account identity.

Trong current runtime:

- `maker8` không consume `channel_id`
- `maker8` cũng không consume `publish.targets[].account_ref` ngoài việc forward

Nghĩa là boundary hiện tại chứa 2 field identity song song nhưng không có rule ràng buộc quan hệ giữa chúng.

Đây là nguồn drift trực tiếp.

### 4.7 Publish/uploader surface area hiện dày hơn hành vi runtime thật

Các field sau tồn tại nhưng không được `maker8` consume theo business semantics:

- `RenderRequest.publish_intent` trong [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py)
- `PublishTarget.variant` trong [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py)
- `ResultDestination.type` trong [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py)

Các field này làm contract dày hơn nhưng không tăng clarity cho current runtime.

### 4.8 Docs cũng đang drift

Có ít nhất 2 lớp doc drift liên quan:

- [`docs/maker8-specs.md`](../docs/maker8-specs.md) vẫn nói nhiều về publisher worker cũ và semantics lịch sử
- [`docs/MAKER8_SOURCE_OF_TRUTH.md`](../docs/MAKER8_SOURCE_OF_TRUTH.md) hiện chứa một số nhận định về schema/examples đã không còn đúng với repo hiện tại

Kết luận:

- không chỉ code drift
- docs cũng drift, làm team rất dễ fix sai chỗ

## 5. Most Likely Root Cause

Từ codebase hiện tại, kết luận hợp lý nhất là:

1. `channel_id` bị thiếu từ upstream request builder hoặc metadata materialization của `editor8`
2. shared contract đang cho phép thiếu vì default là `""`
3. examples/fixtures trong repo đang normalize việc thiếu hoặc lệch format này
4. contract hiện chứa 2 field identity trùng nghĩa nhưng không có invariant

Điểm cần nói rõ:

- đây là suy luận từ repo `maker8`
- tài liệu này không khẳng định chính xác file nào trong `editor8` đang set sai
- nhưng có thể khẳng định `maker8` không phải nơi làm rỗng field này

## 6. Recommended Architectural Direction

### D-1. Chỉ giữ một nguồn sự thật cho publisher account identity

Khuyến nghị:

- dùng `publish.targets[].account_ref` làm field identity canonical
- `uploader_metadata` chỉ nên chứa content metadata dùng chung như `title`, `description`, `tags`, `hashtags`, `lang`

Lý do:

- `account_ref` đã nằm đúng chỗ của publish target
- `channel_id` là tên gọi thiên YouTube-specific
- một object `uploader_metadata` dùng chung cho nhiều platform không nên gánh thêm identity field riêng

### D-2. Không cho phép 2 field identity được edit độc lập

Không nên duy trì trạng thái:

- user hoặc code path A set `channel_id`
- user hoặc code path B set `account_ref`
- hai giá trị có thể khác nhau mà không bị chặn

Nếu cần backward compatibility:

- giai đoạn chuyển tiếp có thể vẫn giữ `uploader_metadata.channel_id`
- nhưng nó phải được derive tự động từ canonical `account_ref`
- không cho nhập tay độc lập

### D-3. `maker8` không nên vá symptom bằng cách tự điền `channel_id`

Không nên fix bằng cách:

- nếu `channel_id` rỗng thì `maker8` tự đoán từ `account_ref`
- hoặc tự thêm fallback magic ở result/manifest

Lý do:

- che giấu bug upstream
- làm contract semantics khó hiểu hơn
- tăng coupling không cần thiết giữa render worker và publish identity logic

### D-4. Contract phải phân loại field rõ ràng

Mỗi field trong boundary phải được dán một trạng thái:

- `ACTIVE`
- `PASS_THROUGH`
- `RESERVED`
- `DEPRECATED`

Không nên giữ field “có vẻ quan trọng” nhưng thực tế không có owner hoặc không có invariant.

## 7. Recommended Fix Strategy

### Phase 1. Contain the issue

Mục tiêu:

- dừng việc để request “publishable” đi qua với `channel_id` rỗng hoặc identity mismatch

Yêu cầu:

- `editor8` phải validate trước khi publish Kafka request
- nếu request có publish target active thì identity canonical phải non-empty
- nếu vẫn còn legacy `uploader_metadata.channel_id`, phải sync tự động từ canonical identity

### Phase 2. Simplify the contract

Mục tiêu:

- loại bỏ duplication giữa `channel_id` và `account_ref`

Khuyến nghị:

- đánh dấu `uploader_metadata.channel_id` là `DEPRECATED`
- giữ `publish.targets[].account_ref` là canonical
- cập nhật examples, fixtures, schemas, tests theo một format duy nhất

### Phase 3. Remove legacy and dead paths

Mục tiêu:

- contract mỏng hơn
- docs dễ hiểu hơn
- runtime behavior khớp với contract surface

Khuyến nghị:

- remove hoặc archive field/doc/path không còn owner
- thu gọn re-export và compatibility shim khi hết nhu cầu migration

## 8. End-to-End Investigation And Fix Checklist

### 8.1 Contract checklist

- xác định field canonical cho publisher identity
- chốt canonical format cho account identity
- cấm tồn tại đồng thời 2 identity field editable độc lập
- ghi rõ field nào `ACTIVE`, field nào `DEPRECATED`, field nào `RESERVED`
- nếu giữ legacy field trong giai đoạn chuyển tiếp, phải có rule derive một chiều

### 8.2 `editor8` request builder checklist

- trace nơi build `uploader_metadata`
- trace nơi build `publish.targets[]`
- xác định field nào đang lấy từ DB, field nào đang lấy từ UI, field nào đang default rỗng
- cấm serialize payload nếu identity canonical bị thiếu
- cấm serialize payload nếu 2 field identity lệch nhau
- log rõ source của metadata materialization ở layer build request

### 8.3 `editor8` persistence and UI checklist

- chỉ có một source of truth cho publisher account identity
- form UI không expose 2 input khác nghĩa cho cùng một concept
- database schema không lưu 2 cột identity song song nếu một cột là legacy
- migration script phải chuẩn hoá prefix/format về một canonical format

### 8.4 `maker8` validation checklist

- nếu current contract vẫn giữ `channel_id`, thêm validation rule cho request publish-ready
- ít nhất phải detect identity mismatch để fail sớm hoặc emit warning rõ ràng
- không thêm logic tự suy đoán identity trong render path

### 8.5 Output and manifest checklist

- render result và manifest phải forward đúng canonical metadata
- không forward cả canonical field lẫn legacy field nếu không có transition rule rõ ràng
- sample output phải phản ánh đúng contract cuối cùng

### 8.6 Testing checklist

- thêm golden fixture cho request có publish target hợp lệ
- thêm golden fixture cho identity mismatch
- thêm golden fixture cho missing identity
- thêm cross-repo contract test giữa `editor8` và `maker8`
- assert examples parse được bằng canonical model
- assert round-trip không làm mất identity canonical

### 8.7 Documentation checklist

- update README
- update schema JSON được generate từ canonical model
- update example request/result
- archive hoặc gắn `legacy` cho docs mô tả publisher flow cũ
- loại bỏ doc nào đang mâu thuẫn với current codebase

## 9. Project-Wide Cleanup Checklist For Consistency

Mục tiêu của checklist này là không chỉ fix `channel_id`, mà dùng cơ hội này để dọn mặt bằng contract và codebase.

### 9.1 Contract surface cleanup

- remove hoặc deprecate `uploader_metadata.channel_id` nếu `account_ref` là canonical
- remove `publish_intent` nếu không có consumer thực tế
- remove `PublishTarget.variant` nếu chưa có publisher worker thật sự dùng
- remove `ResultDestination.type` nếu runtime chỉ support Kafka
- không thêm field mới nếu chưa có owner, invariant và example

### 9.2 Shared model cleanup

- `render_contracts/` phải là canonical package duy nhất cho wire-format models
- `maker8.models.spec` chỉ nên tồn tại tạm thời cho backward compatibility
- khi migration xong, giảm dần import path trung gian và re-export thừa
- không tạo nhiều lớp alias cho cùng một model nếu không thật sự cần

### 9.3 Folder structure cleanup

Khuyến nghị cấu trúc rõ hơn:

- `src/render_contracts/`: canonical wire-format contracts
- `src/maker8/models/`: chỉ giữ maker8-specific output/runtime models
- `docs/canonical/` hoặc equivalent: architecture, contracts, runbook còn hiệu lực
- `docs/investigations/` hoặc `docs/archive/`: guide tạm thời, review cũ, incident notes

Không nên để root `docs/` trộn lẫn:

- doc canonical đang dùng
- review cũ
- instruction card tạm thời
- tài liệu legacy đã hết hiệu lực

### 9.4 Dead code and redundant code cleanup

- xóa enum/class reserved không còn lộ trình rõ ràng
- xóa tests chỉ để giữ field orphan nếu product đã quyết định loại bỏ field
- gộp logic duplicated giữa success path và failure path nếu chúng cùng build một loại output
- xóa code path chỉ tồn tại để duy trì behavior lịch sử không còn ai dùng

### 9.5 Validation cleanup

- invariant phải nằm trong code, không chỉ trong doc
- validation phải dựa trên business intent, không chỉ shape
- field optional phải thật sự optional theo product semantics
- nếu field quan trọng cho downstream mà thiếu sẽ làm flow hỏng, không được cho default rỗng âm thầm

### 9.6 Example and schema hygiene

- schema JSON phải được generate từ canonical model
- examples phải được validate trong CI
- minimal example không được normalize bad practice thành “để trống cũng được” nếu product không chấp nhận
- v1, v2 và golden fixtures phải dùng cùng terminology cho identity fields

### 9.7 Documentation hygiene

- mỗi contract field quan trọng phải có owner
- mỗi doc canonical phải có ngày cập nhật và source of truth rõ ràng
- doc cũ phải được archive thay vì để song song như tài liệu còn hiệu lực
- không duy trì nhiều tài liệu nói khác nhau về cùng một field

## 10. Recommended Review Order

Để tránh sửa lan man, nên review theo thứ tự sau:

1. shared contract trong [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py)
2. examples và generated schemas trong [`docs/examples`](../docs/examples) và [`docs/schemas`](../docs/schemas)
3. `editor8` request builder và UI/persistence mapping
4. `maker8` validation + pass-through output
5. cross-repo tests
6. docs canonical
7. archive/delete legacy docs và compatibility shims không còn cần

## 11. Definition Of Done

Issue này được xem là xử lý đúng khi:

1. không còn request publish-ready nào đi qua với identity canonical rỗng
2. không còn 2 field identity editable độc lập mà không có invariant
3. examples, schemas, fixtures và runtime dùng cùng một canonical format
4. `maker8` không phải đoán hoặc tự vá publisher identity
5. field orphan, reserved, redundant quanh uploader/publish được remove hoặc đánh dấu rõ `DEPRECATED`
6. docs canonical và docs legacy được tách rõ
7. repo có cross-repo contract test để chặn drift quay lại

## 12. Practical Conclusion

Từ repo hiện tại, vấn đề `uploader_metadata.channel_id = ""` không phải bug mutate trong `maker8`. Nó là kết quả của một boundary đang quá permissive và một contract uploader đang có duplication về identity.

Hướng sửa đúng không phải là vá riêng một field, mà là:

- chọn một field identity canonical
- ép invariant ở upstream request builder
- dọn contract surface area thừa
- đồng bộ schema, examples, tests, docs và folder structure theo cùng một source of truth

Nếu làm đúng hướng này, team sẽ không chỉ fix được `channel_id`, mà còn giảm đáng kể interface drift, legacy surface và logic dư thừa trên toàn flow `editor8 -> maker8`.
