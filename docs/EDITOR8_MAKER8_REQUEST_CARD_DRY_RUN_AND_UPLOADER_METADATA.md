# Editor8 / Maker8 Request Card: Dry-Run Flag And Uploader Metadata Contract

## 1. Summary

Hệ thống cần bổ sung một cơ chế đánh dấu video là `dry_run` / `debug-run`.

Yêu cầu nghiệp vụ:

- mặc định `false`
- nếu `dry_run=true` thì pipeline vẫn render video bình thường
- vẫn upload video và manifest lên Dropbox như thường lệ
- nhưng output phải được gắn cờ rõ ràng là `dry_run`
- `editor8` UI phải có chỗ chọn canvas size với 2 preset định sẵn:
  - `short_vertical`
  - `horizontal`
- metadata đi kèm phải đủ giàu để uploader downstream có thể xử lý upload lên:
  - YouTube
  - TikTok
  - Facebook
  - và các platform khác sau này

Card này mô tả đầy đủ contract cần có, ownership của metadata, acceptance criteria, và definition of done.

## 2. Review Of Current State

### 2.1 Điều đã có

Hiện tại hệ thống đã có:

- `render_spec.publish.targets[]`
- mỗi `PublishTarget` có:
  - `platform`
  - `account_ref`
  - `metadata`
  - `params`

Điều này có nghĩa một số thông tin như `title`, `description`, hashtags, visibility, v.v. **đã có chỗ để tồn tại**, ít nhất ở mức raw per-target metadata.

`maker8` hiện cũng đã:

- forward `publish_targets` vào `RenderResult`
- ghi `publish_targets` vào Dropbox manifest

### 2.2 Điều còn thiếu

Tuy nhiên hiện tại vẫn thiếu các thứ sau:

- không có field canonical `dry_run`
- không có semantic rõ ràng cho `debug` vs `dry_run`
- không có uploader-ready manifest rõ ràng ở Dropbox
- metadata cho uploader vẫn quá free-form và phân tán trong `publish.targets[].metadata`
- chưa có object normalized để downstream uploader dùng trực tiếp
- `editor8` hiện chưa có UI-level preset selection rõ ràng cho canvas size
- canvas choice chưa được biểu diễn rõ theo kiểu “preset được chọn” vs “canvas explicit được render”

### 2.3 Kết luận review

Vấn đề không phải là “không có chỗ chứa title/description”.

Vấn đề thật là:

- thiếu **canonical flag** cho dry-run
- thiếu **normalized uploader metadata contract**
- thiếu **ownership rõ ràng** giữa `editor8`, `maker8`, và uploader downstream
- thiếu **canonical UI-to-contract flow** cho canvas preset selection

## 3. Decision Principles

## 3.1 Use one canonical field name

Không nên có hai field song song:

- `debug`
- `dry_run`

Điều đó chỉ tạo thêm drift và ambiguity.

Decision:

- dùng **một field canonical duy nhất**: `dry_run: bool = false`
- nếu UI muốn hiển thị “debug run”, đó chỉ là label ở presentation layer

## 3.2 Dry-run is an execution/distribution flag, not a render-quality flag

`dry_run` không được làm thay đổi:

- chất lượng render
- output file format
- pipeline stages chính

`dry_run` chỉ nói rằng:

- đây là output không nhằm publish production trực tiếp
- downstream uploader phải nhận biết điều này

## 3.3 Metadata ownership must be explicit

Ownership đề xuất:

- `editor8` là nơi author / assemble metadata nội dung
- `maker8` là nơi pass-through + persist metadata sang Dropbox manifest / render result
- uploader downstream là nơi thực thi upload theo metadata đã normalized

`maker8` không nên tự bịa `title` / `description` cho uploader.

## 3.4 Common metadata and per-platform overrides must both exist

Uploader cần hai lớp thông tin:

- common metadata dùng chung cho mọi platform
- per-platform overrides

Không nên ép mọi thứ nhét hết vào `publish.targets[].metadata` rồi để uploader tự đoán.

## 3.5 UI preset selection must not replace explicit render canvas

Canvas preset ở UI là convenience layer cho người dùng.

Contract downstream vẫn phải rõ ràng:

- `render_spec.canvas` là source of truth cho render engine
- nếu cần traceability thì có thể thêm `canvas_profile`
- UI không được chỉ lưu label mà bỏ mất explicit canvas values

Decision đề xuất:

- preset canonical:
  - `short_vertical`
  - `horizontal`
- mapping mặc định:
  - `short_vertical` -> `1080x1920`, `30fps`
  - `horizontal` -> `1920x1080`, `30fps`
- preset mặc định của UI: `short_vertical` để giữ hành vi hiện tại ổn định hơn

## 4. Required Contract Changes

## 4.1 Add canonical top-level `dry_run`

`RenderRequest` phải có field:

```json
"dry_run": false
```

Yêu cầu:

- default = `false`
- backward compatible với payload cũ
- xuất hiện xuyên suốt từ `editor8` sang `maker8`

`RenderResult` và Dropbox manifest cũng phải carry field này.

## 4.2 Add normalized uploader metadata object

Hệ thống phải có một object canonical cho uploader metadata.

Tên có thể là:

- `uploader_metadata`
- hoặc `distribution_metadata`

Nhưng phải thống nhất trên toàn bộ hệ thống.

Object này nên chứa tối thiểu:

- `title`
- `description`
- `lang`
- `tags`
- `hashtags`
- `category`
- `visibility`
- `scheduled_publish_at`
- `content_rating`
- `thumbnail_ref` hoặc `thumbnail_path` nếu có
- `source_attribution` / `credits` nếu có
- `platforms` hoặc per-target mapping

## 4.3 Preserve per-platform target data

`publish.targets[]` vẫn cần tồn tại để downstream biết:

- publish lên platform nào
- account nào
- override metadata nào riêng cho platform đó
- params/platform options nào cần dùng

Nói cách khác:

- `uploader_metadata` = normalized common layer
- `publish.targets[]` = routing + per-platform override layer

## 4.4 Add uploader-ready metadata into Dropbox manifest

Manifest được upload cùng video lên Dropbox phải chứa:

- `dry_run`
- `uploader_metadata`
- `publish_targets`
- output/dropbox refs
- content info đủ để uploader downstream xử lý mà không cần query ngược về `editor8`

Dropbox manifest phải là source-of-truth thực dụng cho uploader downstream.

## 4.5 Add dry-run and uploader metadata into RenderResult

`RenderResult` Kafka payload cũng phải carry:

- `dry_run`
- `uploader_metadata`
- `publish_targets`

để downstream services không bị ép phải đọc Dropbox manifest nếu chỉ cần route/decision nhanh.

## 4.6 Add canonical canvas preset selection

`editor8` UI phải có field chọn canvas preset với đúng 2 lựa chọn định sẵn:

- `short_vertical`
- `horizontal`

Yêu cầu contract:

- `render_spec.canvas` vẫn phải được populate đầy đủ bằng giá trị explicit
- nên thêm `canvas_profile` canonical để manifest/result/downstream biết preset nào đã được chọn
- `canvas_profile` phải là additive và backward compatible

Giá trị mapping mặc định:

- `short_vertical` -> `w=1080`, `h=1920`, `fps=30`
- `horizontal` -> `w=1920`, `h=1080`, `fps=30`

Nếu sau này muốn mở rộng thêm preset khác thì phải qua contract review mới, không hardcode ad-hoc trong UI.

## 5. Required Behavior

## 5.1 Behavior when `dry_run=false`

Pipeline hoạt động như production bình thường:

- render
- upload Dropbox
- emit result
- metadata mang trạng thái production-ready

## 5.2 Behavior when `dry_run=true`

Pipeline vẫn phải:

- render bình thường
- upload `.mp4`
- upload manifest
- emit `RenderResult`

Nhưng output phải có:

- `dry_run=true`
- metadata/manifest đủ rõ để downstream uploader biết đây là non-production artifact

Quan trọng:

- `dry_run=true` không được làm pipeline bỏ qua render/upload
- không được silently downgrade output

## 5.3 Uploader behavior compatibility

Manifest/result phải đủ thông tin để uploader downstream:

- quyết định skip publish production
- hoặc publish vào sandbox/test account
- hoặc show preview/review UI
- hoặc route sang một workflow kiểm duyệt riêng

## 6. Recommended Metadata Shape

## 6.1 Common uploader metadata

Ví dụ:

```json
"uploader_metadata": {
  "title": "Iran vs Israel: điều gì xảy ra tiếp theo?",
  "description": "Phân tích ngắn về leo thang xung đột và tác động giá dầu.",
  "lang": "vi-VN",
  "tags": ["iran", "israel", "gia dau", "trung dong"],
  "hashtags": ["#Iran", "#Israel", "#OilPrice"],
  "category": "news",
  "visibility": "private",
  "scheduled_publish_at": null,
  "content_rating": "general",
  "thumbnail_ref": "",
  "credits": []
}
```

## 6.2 Per-platform targets

Ví dụ:

```json
"publish_targets": [
  {
    "platform": "youtube",
    "account_ref": "yt_main",
    "metadata": {
      "title": "Iran vs Israel: điều gì xảy ra tiếp theo?",
      "description": "Bản đầy đủ cho YouTube",
      "privacy_status": "private"
    },
    "params": {
      "playlist_id": "PL123"
    }
  },
  {
    "platform": "tiktok",
    "account_ref": "tt_main",
    "metadata": {
      "caption": "Leo thang Trung Đông và giá dầu"
    },
    "params": {}
  }
]
```

## 7. Required Code Changes

## 7.1 `render_contracts`

Phải cập nhật canonical contract để thêm:

- `RenderRequest.dry_run: bool = false`
- `RenderResult.dry_run: bool = false`
- object `uploader_metadata` canonical

Nếu cần version bump hoặc additive schema update, phải làm rõ chiến lược backward compatibility.

## 7.2 `editor8`

`editor8` phải:

- author `dry_run`
- author / assemble `uploader_metadata`
- preserve existing per-target metadata
- publish render request với field mới
- thêm UI control để chọn canvas preset
- map UI preset sang `render_spec.canvas` explicit
- nếu dùng `canvas_profile`, phải publish field này nhất quán

`editor8` cũng phải quyết định source-of-truth cho:

- title
- description
- tags
- lang
- scheduling metadata
- canvas preset được chọn

## 7.3 `maker8`

`maker8` phải:

- carry `dry_run` trong `PipelineContext`
- persist `dry_run` vào manifest
- persist `dry_run` vào `RenderResult`
- persist `uploader_metadata` vào manifest
- persist `uploader_metadata` vào `RenderResult`
- keep `publish_targets` intact
- preserve `render_spec.canvas` as-is
- nếu có `canvas_profile`, persist field này vào manifest/result để downstream dễ route asset/publish policy

`maker8` không được làm mất metadata khi đi qua render/upload stages.

## 7.4 Dropbox manifest

Manifest JSON trong Dropbox phải được nâng cấp từ “render artifact manifest” thành “uploader-ready manifest”.

Nó phải đủ giàu để downstream uploader không phải đoán cấu trúc từ `publish_targets[].metadata` rời rạc.

## 8. Best Practices That Must Be Applied

## 8.1 One canonical boolean, no synonyms

Chỉ dùng `dry_run`.
Không thêm song song `debug`, `is_debug`, `debug_mode`, v.v.

## 8.2 Keep backward compatibility

Payload cũ không có `dry_run` phải tiếp tục hoạt động và mặc định `false`.

## 8.3 Separate common metadata from per-platform overrides

Đây là nguyên tắc quan trọng nhất để uploader downstream dễ maintain.

## 8.4 Persist enough metadata for offline processing

Uploader downstream không nên bị phụ thuộc tuyệt đối vào DB `editor8`.
Dropbox manifest và Kafka result phải đủ giàu cho asynchronous workflows.

## 8.5 Dry-run must be explicit in every downstream artifact

Không chỉ trong request.

`dry_run` phải xuất hiện ở:

- request
- result
- manifest

để không service nào phải suy đoán.

## 8.6 UI preset and render canvas must stay in sync

Nếu UI cho người dùng chọn preset, thì:

- preset label
- explicit `render_spec.canvas`
- manifest/result metadata

phải nhất quán với nhau.

Không được để UI hiển thị “horizontal” nhưng request thực tế vẫn render theo vertical mặc định.

## 9. Acceptance Criteria

### AC1. Backward compatibility

Khi request không có `dry_run`, hệ thống:

- parse thành công
- mặc định `dry_run=false`
- render/upload/result không thay đổi behavior cũ

### AC2. Dry-run propagation

Khi request có `dry_run=true`, hệ thống:

- render video bình thường
- upload video bình thường lên Dropbox
- upload manifest bình thường lên Dropbox
- emit `RenderResult` bình thường
- mọi output downstream đều có `dry_run=true`

### AC3. Uploader metadata persistence

Khi request có `uploader_metadata` và `publish_targets`, thì:

- `maker8` không làm mất field nào
- manifest Dropbox chứa đủ các field uploader cần
- `RenderResult` cũng chứa metadata này

### AC4. Per-platform metadata preserved

`publish_targets[].metadata` và `publish_targets[].params` phải được giữ nguyên qua pipeline trừ khi có normalization được định nghĩa rõ.

### AC5. Uploader downstream can act without extra DB lookup

Một service uploader downstream nhận:

- Dropbox manifest
- hoặc `RenderResult`

phải có đủ dữ liệu để:

- biết đây có phải `dry_run` không
- biết `title`, `description`, `lang`
- biết target platforms/account refs
- biết per-platform overrides

### AC6. No ambiguity between debug and dry-run

Codebase, schema, docs, tests chỉ dùng **một khái niệm canonical** cho feature này: `dry_run`.

### AC7. Canvas preset selection in `editor8` UI

`editor8` UI phải hiển thị đúng 2 preset:

- `Short Video (Vertical)`
- `Horizontal Video`

Khi người dùng chọn:

- `Short Video (Vertical)` thì request downstream phải có `render_spec.canvas = 1080x1920@30`
- `Horizontal Video` thì request downstream phải có `render_spec.canvas = 1920x1080@30`

Nếu có `canvas_profile`, nó cũng phải khớp với preset được chọn.

### AC8. Canvas preset propagation

Manifest Dropbox và `RenderResult` phải cho downstream biết được video này thuộc profile nào hoặc ít nhất mang explicit canvas values đúng với preset đã chọn.

## 10. Non-Goals

Card này không yêu cầu:

- triển khai uploader YouTube/TikTok/Facebook ngay trong `maker8`
- thay đổi chất lượng render khi `dry_run=true`
- thay đổi routing publish business logic ngoài phạm vi contract/metadata

## 11. Definition Of Done

Chỉ được coi là hoàn thành khi:

- canonical contract đã có `dry_run`
- canonical contract đã có `uploader_metadata`
- `editor8` publish được field này
- `maker8` carry/persist được field này qua result + Dropbox manifest
- `editor8` UI có preset selector cho `short_vertical` và `horizontal`
- preset selection map đúng sang explicit canvas values trong request
- nếu có `canvas_profile`, field này được preserve nhất quán qua request/result/manifest
- docs/examples/schemas được cập nhật
- tests khóa được default false, propagation, backward compatibility, metadata preservation
- uploader downstream có thể đọc manifest/result và xử lý mà không cần đoán ngữ nghĩa

## 12. Final Requirement

Yêu cầu cuối cùng của card này là:

> một video `dry_run` vẫn phải được render và upload đầy đủ như bình thường, nhưng toàn bộ artifact downstream phải được gắn cờ rõ ràng và mang metadata đủ giàu để uploader xử lý publish hoặc non-publish workflow một cách đáng tin cậy.
