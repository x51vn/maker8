# Scene Count, Duration Fit, And Required Asset Review Guide

## 1. Purpose

Tài liệu này hướng dẫn review và verify 3 câu hỏi quan trọng:

1. số lượng `scenes` có đang bị fixed bởi assumption cứng hay không
2. số lượng `scenes` của mỗi video có thực sự phù hợp với độ dài video hay không
3. mọi `scene` có bắt buộc phải có visual assets để video cuối luôn có hình ảnh hay không

Tài liệu này cũng bổ sung một yêu cầu cleanup cross-project:

- loại bỏ các phần liên quan đến MCP servers và Dropbox khỏi `editor8-frontend`

Đây là guide review và verification. Đây không phải implementation plan chi tiết.

## 2. Project Context

Hệ thống hiện tại là flow `editor8 -> maker8`.

```text
editor8
  -> plan content và build RenderRequest
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
```

Boundary cần nhớ:

- `editor8` là upstream planner / request builder
- `maker8` là downstream renderer
- repo hiện tại là repo `maker8`, nên mọi kết luận về upstream được suy ra từ shared contract, tests, examples và runtime behavior của `maker8`

## 3. Verified Current State From This Codebase

### 3.1 `maker8` hiện không hard-code 5 scenes

Các bằng chứng đã xác minh:

- [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) định nghĩa `RenderSpec.scenes` là `list[Scene]`, không có `max = 5`
- [`src/maker8/pipeline/validate.py`](../src/maker8/pipeline/validate.py) chỉ enforce:
  - có ít nhất 1 scene
  - `scene_id` unique
  - `narration.text` không rỗng
- [`tests/test_variable_scenes.py`](../tests/test_variable_scenes.py) chứng minh parse và validate được `1, 2, 3, 5, 7, 10, 15` scenes

Kết luận:

- `maker8` renderer không phải nơi khóa hệ thống ở 5 scenes
- nếu hệ thống thực tế vẫn hay ra 5 scenes, khả năng cao assumption đó đang nằm ở upstream planning hoặc request generation

### 3.2 Current system chưa đảm bảo scene count phù hợp với video duration

Các bằng chứng đã xác minh:

- `maker8` không tự tính scene count từ duration; nó chỉ render `render_spec.scenes[]` đã nhận
- [`src/maker8/pipeline/validate.py`](../src/maker8/pipeline/validate.py) chỉ kiểm tra `planning.planned_scene_count == len(render_spec.scenes)` khi metadata đó có mặt
- [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) có `planning.target_duration_sec`, `scene_count_policy_version`, `planned_scene_count`, nhưng đây mới chỉ là metadata audit chứ không phải planner runtime
- [`src/maker8/rendering/composer.py`](../src/maker8/rendering/composer.py) hiện tính duration mỗi scene theo:
  - `scene.duration`
  - hoặc TTS duration
  - hoặc fallback `5.0s`

Kết luận:

- current codebase không chứng minh rằng scene count đang được derive hợp lý từ total video duration
- requirement “scene count phải phù hợp với độ dài video” là requirement cross-system, với ownership chính nằm ở upstream planner của `editor8`

### 3.3 Current system chưa đảm bảo mọi scene đều có visual assets

Các bằng chứng đã xác minh:

- [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) định nghĩa `Layer.required = False` và `missing_asset_policy = "drop_layer"` là default
- [`src/maker8/pipeline/render.py`](../src/maker8/pipeline/render.py) coi scene là viable nếu:
  - có text layer
  - hoặc có ít nhất một `asset_ref` resolve được
- [`tests/test_survivability.py`](../tests/test_survivability.py) hiện có test `test_text_only_scene_always_viable`
- [`src/maker8/rendering/composer.py`](../src/maker8/rendering/composer.py) luôn tạo background `ColorClip` theo `canvas.bg`

Kết quả hiện tại:

- scene text-only vẫn có thể đi qua
- scene mất visual chính nhưng còn text vẫn có thể đi qua
- background đen mặc định có thể trở thành output cuối nếu visual layer bị mất

Kết luận:

- current behavior chưa đáp ứng yêu cầu “tất cả scenes đều bắt buộc phải có assets để video cuối luôn luôn có hình ảnh”
- đây là behavioral change rõ ràng, không phải chỉ là documentation change

## 4. Review Goals

Guide này yêu cầu team review và verify để chốt 4 điều:

1. không còn assumption ngầm nào rằng video luôn có 5 scenes
2. `scene_count` được suy ra từ duration theo một policy rõ ràng và audit được
3. mọi scene trong request production đều có ít nhất một visual asset bắt buộc
4. `editor8-frontend` không còn chứa responsibility liên quan đến MCP servers hoặc Dropbox

## 5. Review Scope

### 5.1 In scope

- shared contract giữa `editor8` và `maker8`
- examples, fixtures, tests, docs
- upstream planning / request generation logic
- downstream validation và render degradation behavior
- frontend/backend responsibility boundary của `editor8`

### 5.2 Out of scope

- công thức duration-to-scene cuối cùng ở mức implementation detail
- UI design chi tiết cho scene planner
- migrate code của `editor8` trong repo này, vì repo hiện tại không chứa source code đó

## 6. End-to-End Review And Verification Checklist

### 6.1 Verify scene count is not fixed

Checklist:

- search toàn bộ `editor8` để tìm constant hoặc prompt assumption kiểu `5 scenes`, `5 scene`, `exactly 5`, `five scenes`
- review planner, prompt templates, assembler, request builder, preset config và UI defaults
- verify không có fallback silent nào tự ép `scene_count = 5` khi duration thiếu hoặc planning fail
- verify examples, fixtures và smoke tests không normalize behavior “5 scenes là mặc định”

Recommended searches:

```bash
rg -n "5 scenes|5 scene|five scenes|scene_count|planned_scene_count|target_duration|output_length_target_seconds" .
```

Pass condition:

- không còn bất kỳ code path production nào hard-code số `5` như scene count mặc định

### 6.2 Verify scene count fits video duration

Checklist:

- xác định upstream đang dùng nguồn duration nào:
  - target video duration
  - estimated narration duration
  - script duration budget
  - source media duration
- chốt policy rõ ràng cho `seconds_per_scene`, `min_scenes`, `max_scenes`, `rounding`
- log hoặc materialize metadata sau planning:
  - `target_duration_sec`
  - `scene_count_policy_version`
  - `planned_scene_count`
  - `estimated_seconds_per_scene`
- verify `planned_scene_count == len(render_spec.scenes)`
- verify output request có thể audit lại được vì sao planner chọn số scene đó

Pass condition:

- với cùng policy và cùng duration input, planner luôn cho ra scene count nhất quán, bounded và explainable

### 6.3 Verify every scene has required visual assets

Checklist:

- mỗi scene production phải có ít nhất một layer `image` hoặc `video`
- layer visual chính phải có semantic role rõ ràng, ví dụ `primary_visual`
- layer visual chính phải có `required = true`
- request validation phải fail nếu một scene không có visual asset bắt buộc
- không cho phép text-only scene trong production path
- không cho phép narrator-only scene trong production path nếu business yêu cầu video luôn có hình

Pass condition:

- mọi scene hợp lệ đều có visual asset bắt buộc và có thể resolve được

### 6.4 Verify runtime never degrades into black or text-only scenes

Checklist:

- review `missing_asset_policy` của mọi required visual layer
- không dùng default permissive nếu business yêu cầu “luôn có hình”
- cấm `drop_layer` cho visual chính bắt buộc
- không coi scene là viable chỉ vì còn text layer nếu visual chính bị mất
- review các test hiện đang normalize degradation:
  - text-only scene viable
  - scene skipped vì mất assets
  - placeholder scene

Pass condition:

- runtime không thể tạo output “nền đen + text” cho scene production

## 7. Required Product And Contract Changes

### R-1. Scene count must be duration-aware

Yêu cầu mới:

- `scene_count` phải được derive từ duration policy thay vì constant
- planner phải emit rõ `planned_scene_count`
- request contract phải cho phép audit policy decision

### R-2. All production scenes must have a required visual asset

Yêu cầu mới:

- mỗi scene phải có ít nhất một `image` hoặc `video` layer được đánh dấu là visual chính
- visual chính phải là `required = true`
- request phải bị reject nếu scene thiếu visual chính

### R-3. Missing required visual asset must fail the request or force regeneration

Yêu cầu mới:

- nếu visual chính không resolve được, hệ thống không được im lặng degrade thành text-only hoặc black scene
- business path production nên chọn một trong hai hướng:
  - fail request
  - hoặc regenerate trước khi render

Khuyến nghị:

- không dùng `skip_scene` cho path production nếu mục tiêu là giữ structure video ổn định
- không dùng `scene_placeholder` cho path production nếu mục tiêu là “video cuối luôn luôn có hình ảnh thật”

### R-4. Text-only scene must become a legacy or explicitly forbidden pattern

Yêu cầu mới:

- coi text-only scene là legacy/testing-only pattern
- loại bỏ nó khỏi production examples, fixtures và acceptance tests

## 8. Current Gaps That Must Be Closed

### G-1. Validation gap

Current gap:

- `ValidateStage` chưa enforce rằng scene phải có visual asset bắt buộc

Required action:

- thêm invariant ở contract validation hoặc request assembly layer

### G-2. Runtime viability gap

Current gap:

- `RenderStageImpl` hiện coi text layer là đủ để scene “viable”

Required action:

- tách rõ production viability khỏi degraded legacy viability

### G-3. Testing gap

Current gap:

- repo đang có test chứng minh variable scene count
- nhưng chưa có test chứng minh scene count fit với duration policy
- và còn có test normalize text-only scene

Required action:

- thêm test duration buckets
- thêm test reject scene without primary visual
- archive hoặc rewrite tests đang giữ behavior legacy

### G-4. Example and fixture gap

Current gap:

- minimal/example payloads hiện chưa đủ mạnh để cấm text-only scenes hoặc missing visual primary asset

Required action:

- update examples để mọi scene đều có visual asset bắt buộc

## 9. Recommended Verification Commands

Trong repo `maker8`, lớp baseline nên kiểm tra tối thiểu:

```bash
pytest -q tests/test_variable_scenes.py tests/test_v2_contract.py tests/test_survivability.py
```

Trong `editor8`, cần thêm lớp verify tương ứng:

```bash
rg -n "5 scenes|5 scene|planned_scene_count|target_duration|output_length_target_seconds" .
pytest -q
```

Yêu cầu bắt buộc:

- có cross-repo contract test giữa `editor8` và `maker8`
- examples phải parse được bằng canonical model trong [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py)

## 10. Cleanup Requirement For `editor8-frontend`

Đây là requirement mới cần bổ sung rõ vào review.

### F-1. Remove MCP server concerns from `editor8-frontend`

`editor8-frontend` không nên chứa responsibility liên quan đến MCP servers.

Yêu cầu:

- không chứa UI hoặc config chuyên biệt để vận hành MCP servers
- không chứa env vars, pages, components hoặc flows chỉ để quản lý MCP server lifecycle
- nếu cần integration, frontend chỉ gọi API backend ở mức product feature, không gánh transport/system orchestration

### F-2. Remove Dropbox concerns from `editor8-frontend`

`editor8-frontend` không nên chứa responsibility liên quan đến Dropbox OAuth hoặc Dropbox system integration.

Yêu cầu:

- loại bỏ pages, routes, state và UI flows chuyên biệt cho Dropbox connection trong frontend
- loại bỏ frontend-owned Dropbox OAuth handling
- loại bỏ frontend env/config liên quan tới Dropbox app credentials
- nếu Dropbox còn tồn tại trong hệ thống, nó phải là backend/platform concern chứ không phải frontend concern

### F-3. Enforce clean frontend boundary

Boundary mới cần rõ:

- `editor8-frontend`: chỉ là product UI
- `editor8-backend` hoặc service layer: chịu trách nhiệm integrations, credentials, orchestration

Pass condition:

- `editor8-frontend` không còn MCP server code path
- `editor8-frontend` không còn Dropbox-specific code path
- docs và deployment không còn mô tả frontend như nơi chịu trách nhiệm cho hai concern này

## 11. Project-Wide Consistency Checklist

### C-1. Contract consistency

- `render_contracts/` là source of truth duy nhất cho wire-format
- examples, fixtures, docs phải bám contract này
- không chấp nhận docs nói một kiểu, examples một kiểu, runtime một kiểu

### C-2. Planning consistency

- scene planning ownership phải nằm rõ ở upstream
- renderer không tự “sửa” lại scene count
- metadata planning phải đủ để audit

### C-3. Visual consistency

- mọi production scene phải có visual asset thật
- không normalize black scene, placeholder scene hoặc text-only scene như acceptable output

### C-4. Frontend boundary consistency

- frontend không ôm integration concerns như MCP servers hoặc Dropbox
- secrets, OAuth, connectors và orchestration phải ở backend/platform

### C-5. Legacy cleanup consistency

- remove hoặc archive tests, examples, docs và code paths đang giữ behavior legacy
- không để cùng lúc tồn tại:
  - policy mới yêu cầu mandatory visual assets
  - nhưng tests/examples vẫn hợp thức hóa text-only scene

## 12. Definition Of Done

Review này được xem là hoàn tất khi:

1. team chứng minh được không còn hard-code `5 scenes` trong production path
2. có policy rõ ràng và audit được để derive `scene_count` từ duration
3. mọi production scene đều có visual asset bắt buộc
4. runtime không còn tạo black scene hoặc text-only scene như output hợp lệ
5. examples, fixtures, docs và tests đã được cập nhật theo behavior mới
6. `editor8-frontend` không còn phần liên quan đến MCP servers
7. `editor8-frontend` không còn phần liên quan đến Dropbox

## 13. Practical Conclusion

Từ repo hiện tại có thể chốt rõ:

- `maker8` không hard-code số 5 cho scene count
- nhưng hệ thống hiện chưa chứng minh được rằng scene count đang fit đúng với video duration
- và current runtime vẫn chưa bảo đảm mọi scene đều có visual assets

Vì vậy, review đúng không chỉ là tìm xem có constant `5` hay không. Review đúng là phải đi xuyên toàn flow `editor8 -> maker8`, chốt duration-aware planning, bắt buộc hóa visual assets ở từng scene, và dọn boundary của `editor8-frontend` để nó không còn ôm MCP servers hoặc Dropbox.
