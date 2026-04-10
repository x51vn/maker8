# PRD: Adaptive Scene Count, Explicit Visual Requirements, And Optional Subtitles For Maker8 Requests

## 1. Mục tiêu

Tài liệu này chốt requirements và hướng redesign schema giữa upstream system và `maker8` cho 3 vấn đề đang còn lẫn nhau:

1. hệ thống thực tế vẫn đang hành xử như thể bị khóa ở `5 scenes`
2. một số scene vẫn render ra nền đen do thiếu file ảnh/video
3. subtitle đang xuất hiện như hành vi mặc định thay vì là option rõ ràng, mặc định phải là không có

Đây là PRD cho request contract truyền sang `maker8`, không phải implementation plan chi tiết.

## 2. Current State Đã Xác Minh Từ Codebase

### 2.1 `maker8` renderer không hard-code 5 scene

Current canonical contract nằm ở [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py).

Các bằng chứng đã xác minh:

- `RenderSpec.scenes` chỉ là `list[Scene]`, không có max = 5 trong model.
- `ValidateStage` chỉ enforce:
  - có ít nhất 1 scene
  - `scene_id` unique
  - `narration.text` không rỗng
  trong [`src/maker8/pipeline/validate.py`](../src/maker8/pipeline/validate.py).
- Repo có regression test chứng minh parse/validate được `1, 3, 5, 7, 10, 15` scenes trong [`tests/test_variable_scenes.py`](../tests/test_variable_scenes.py).

Kết luận:

- vấn đề "fixed 5 scenes" không nằm ở renderer core của `maker8`
- vấn đề nằm ở upstream planning / request generation / schema semantics của hệ gửi request sang `maker8`

### 2.2 Scene nền đen hiện là degradation có thật trong runtime

Các bằng chứng đã xác minh:

- `Canvas.bg` mặc định là `#000000` trong [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py).
- Mỗi scene luôn được khởi tạo với `ColorClip` background theo `canvas.bg` trong [`src/maker8/rendering/composer.py`](../src/maker8/rendering/composer.py).
- Nếu một layer `image` hoặc `video` bị thiếu asset ở runtime, layer đó bị drop và cảnh báo `LAYER_ASSET_MISSING` trong [`src/maker8/rendering/composer.py`](../src/maker8/rendering/composer.py).
- Scene vẫn được xem là "viable" nếu nó còn:
  - bất kỳ `text` layer nào
  - hoặc ít nhất một `asset_ref` resolve được
  theo [`src/maker8/pipeline/render.py`](../src/maker8/pipeline/render.py).

Hệ quả hiện tại:

- nếu scene mất visual chính nhưng còn text layer, video vẫn render thành công
- background đen mặc định vẫn tồn tại
- kết quả là scene có thể thành "nền đen + text", đúng symptom user đang thấy

### 2.3 Subtitle hiện không phải là capability riêng trong contract

Các bằng chứng đã xác minh:

- Canonical schema hiện không có field `subtitle` hoặc `subtitles`; contract chỉ có `scene.layers[]` generic.
- Example request encode subtitle như một `text` layer tên `subtitle-text` trong [`docs/examples/render_request.example.json`](./examples/render_request.example.json).
- Specs doc cũng mô tả caption/subtitle bằng `text` layer generic trong [`docs/maker8-specs.md`](./maker8-specs.md).

Kết luận:

- `maker8` hiện không có khái niệm "subtitle" ở layer schema
- subtitle chỉ xuất hiện khi upstream nhét thêm `text layer`
- vì schema không phân biệt `title`, `overlay text`, `subtitle`, nên default behavior dễ bị drift và khó kiểm soát

## 3. Problem Statement

Current request contract đang mô tả chủ yếu theo primitive render (`layers[]`, `assets[]`, `audio_tracks[]`) nhưng thiếu semantic intent.

Thiếu semantic này gây ra 3 lỗi sản phẩm:

1. upstream có thể tiếp tục plan theo tư duy `5 scenes` mà downstream không audit được
2. hệ thống không biết visual nào là bắt buộc, visual nào chỉ là optional overlay
3. hệ thống không biết subtitle là:
   - một capability riêng
   - hay chỉ là một text layer bất kỳ

Khi contract chỉ biểu diễn primitive, downstream khó phân biệt:

- scene cố ý text-only
- scene đáng lẽ phải có ảnh/video chính nhưng asset bị mất
- scene có title overlay
- scene có subtitle

Đây là lý do hiện tại vẫn có hiện tượng:

- pacing như thể bị khóa 5 scene
- nền đen do missing visual nhưng request vẫn "hợp lệ"
- subtitle xuất hiện như default behavior

## 4. Product Principles

### P-1. Scene count là trách nhiệm của upstream planner

`maker8` là renderer. Nó phải render đúng `render_spec.scenes[]` được gửi sang và không tự tái chia scene.

### P-2. Contract truyền sang `maker8` phải biểu diễn intent, không chỉ primitive

Schema phải cho downstream biết:

- scene được plan ra bao nhiêu và vì sao
- visual nào là visual chính
- visual nào được phép mất
- subtitle có bật hay không

### P-3. Subtitle phải là opt-in rõ ràng

Default phải là không subtitle.

Nếu request không nói gì về subtitle, output không được tự suy luận và hiển thị subtitle.

### P-4. Missing visual bắt buộc không được degrade âm thầm thành nền đen

Nếu một scene có visual chính bắt buộc mà asset bị thiếu, hệ thống phải có policy rõ ràng:

- fail scene
- skip scene
- render placeholder

Nhưng không được mặc định rơi vào trạng thái "nền đen nhưng vẫn coi là render ổn" nếu business intent là phải có visual.

## 5. Scope

### In scope

- redesign request schema truyền sang `maker8`
- làm rõ ownership của `scene_count`
- đưa subtitle thành một capability optional, default off
- đưa yêu cầu visual bắt buộc vào contract
- bổ sung metadata để audit quyết định planning

### Out of scope

- công thức duration-to-scene cuối cùng
- UI/editor của upstream
- chi tiết layout subtitle cuối cùng
- implementation code cụ thể ở `editor8` hoặc `maker8`

## 6. Proposed Contract Direction

## 6.1 Versioning

Vì đây là contract change có tính breaking ở semantics, request schema mới phải được version hóa.

Yêu cầu:

- thêm `spec_version = "2.0"` cho envelope `RenderRequest`
- thêm `render_spec.spec_version = "2.0"`
- `maker8` phải hỗ trợ đọc song song `v1` và `v2` trong giai đoạn migration

Ghi chú:

- có thể giữ Kafka topic hiện tại và route theo `spec_version`
- hoặc tạo topic version mới nếu team cần parallel rollout chặt hơn
- quyết định topic version là deployment choice; requirement bắt buộc là schema change phải được version hóa rõ ràng

## 6.2 Root-Level Planning Metadata

Để chấm dứt ambiguity kiểu "tại sao vẫn ra 5 scenes", request mới phải có planning metadata ở envelope.

Đề xuất thêm field:

```json
{
  "planning": {
    "target_duration_sec": 30,
    "duration_source": "target_video_duration",
    "scene_count_policy_version": "duration_bucket_v1",
    "planned_scene_count": 7
  }
}
```

### Requirement

- `planning.planned_scene_count` phải bằng `len(render_spec.scenes)`
- `maker8` không cần dùng metadata này để render
- nhưng phải log/emit metadata này để audit

### Lý do

- biến decision "scene count" từ hidden assumption thành explicit contract metadata
- giúp phát hiện upstream vẫn đang bám policy 5 scene

## 6.3 Subtitle Phải Trở Thành Capability Riêng

Thay vì encode subtitle như text layer generic, `v2` phải có schema riêng cho subtitle.

### Đề xuất thêm vào `defaults`

```json
{
  "defaults": {
    "subtitles": {
      "enabled": false,
      "source": "narration",
      "max_lines": 2
    }
  }
}
```

### Đề xuất thêm vào `scene`

```json
{
  "scene_id": "scene_01",
  "subtitle": null
}
```

hoặc nếu bật:

```json
{
  "scene_id": "scene_01",
  "subtitle": {
    "enabled": true,
    "source": "narration"
  }
}
```

### Required semantics

- `defaults.subtitles.enabled = false` là default toàn request
- `scene.subtitle = null` nghĩa là scene này không render subtitle
- `scene.subtitle.enabled = true` mới bật subtitle cho scene đó
- nếu `scene.subtitle` không có, scene inherit từ `defaults.subtitles`
- `subtitle` không được tự động xuất hiện chỉ vì scene có `narration.text`

### Allowed subtitle sources

- `narration`: dùng `scene.narration.text`
- `custom`: dùng `scene.subtitle.text`

### Tác động lên `layers[]`

Trong `v2`:

- `text` layer vẫn được giữ cho:
  - title
  - CTA
  - label
  - watermark text
  - decorative copy
- nhưng subtitle không còn nên được encode mặc định như một `text layer`

### Deprecation rule

`role = "subtitle"` trong `layers[]` được xem là legacy transitional pattern và không phải pattern chính thức của `v2`.

## 6.4 Bổ Sung Semantic Role Cho `layers[]`

Để phân biệt visual chính và overlay phụ, `Layer` trong `v2` phải có semantic role.

Đề xuất thêm fields:

```json
{
  "layer_id": "bg_01",
  "type": "image",
  "asset_ref": "img_01",
  "role": "primary_visual",
  "required": true,
  "missing_asset_policy": "scene_placeholder"
}
```

### Allowed roles tối thiểu

- `primary_visual`
- `supporting_visual`
- `title`
- `logo`
- `cta`
- `decorative_text`

### Required semantics

- `primary_visual` là visual chính mà business kỳ vọng scene phải có
- `supporting_visual` là visual phụ, được phép degrade dễ hơn
- text layer không phải subtitle phải có role phù hợp, không mơ hồ

## 6.5 Explicit Missing Visual Policy

Để chặn hiện tượng scene nền đen khi visual chính bị mất, schema phải cho phép diễn đạt missing visual policy.

### Allowed values tối thiểu

- `drop_layer`
- `skip_scene`
- `scene_placeholder`
- `fail_request`

### Default policy theo role

- `primary_visual`:
  - `required = true`
  - recommended default `missing_asset_policy = "scene_placeholder"`
- `supporting_visual`:
  - `required = false`
  - recommended default `missing_asset_policy = "drop_layer"`

### Required behavior

Nếu layer có `required = true` và asset không resolve được ở runtime:

- hệ thống phải áp dụng đúng `missing_asset_policy`
- đồng thời emit warning/error có cấu trúc
- không được âm thầm rơi về "chỉ còn background đen mặc định" trừ khi policy explicit cho phép

## 6.6 V2 Sample Shape

```json
{
  "job_id": "job-001",
  "spec_version": "2.0",
  "planning": {
    "target_duration_sec": 30,
    "duration_source": "target_video_duration",
    "scene_count_policy_version": "duration_bucket_v1",
    "planned_scene_count": 3
  },
  "render_spec": {
    "spec_version": "2.0",
    "canvas": {
      "w": 1080,
      "h": 1920,
      "fps": 30,
      "bg": "#000000"
    },
    "defaults": {
      "narration": {
        "lang": "vi-VN",
        "tts_preset_ref": "tts:vi:default"
      },
      "scene_timing": {
        "head_pad_sec": 0.15,
        "tail_pad_sec": 0.45,
        "duration_mode": "auto_from_tts"
      },
      "subtitles": {
        "enabled": false,
        "source": "narration",
        "max_lines": 2
      }
    },
    "assets": [
      {
        "id": "img_01",
        "type": "image",
        "source": {
          "kind": "http",
          "url": "https://example.com/scene-01.jpg"
        }
      }
    ],
    "scenes": [
      {
        "scene_id": "scene_01",
        "narration": {
          "text": "Đây là cảnh mở đầu."
        },
        "layers": [
          {
            "layer_id": "bg_01",
            "type": "image",
            "asset_ref": "img_01",
            "role": "primary_visual",
            "required": true,
            "missing_asset_policy": "scene_placeholder",
            "rect": { "x": 0, "y": 0, "w": 1080, "h": 1920 },
            "fit": "cover"
          },
          {
            "layer_id": "title_01",
            "type": "text",
            "role": "title",
            "text": "MỞ ĐẦU",
            "rect": { "x": 80, "y": 1400, "w": 920, "h": 180 },
            "text_align": "center"
          }
        ],
        "subtitle": null
      }
    ]
  }
}
```

## 7. Functional Requirements

### F-1. Không còn assumption chính thức nào rằng mọi request có 5 scene

- schema không được document default = 5
- examples không được toàn bộ cố định 5
- test contract phải cover nhiều số scene

### F-2. Upstream phải phát ra số scene cuối cùng một cách explicit

- `planning.planned_scene_count` là required trong `v2`
- `render_spec.scenes[]` phải khớp đúng số đó

### F-3. Subtitle mặc định phải tắt

- nếu request không set `defaults.subtitles.enabled = true`
- và không set `scene.subtitle.enabled = true`
- output không được có subtitle

### F-4. Subtitle không được ẩn trong `layers[]` như default behavior

- `layers[]` vẫn hợp lệ cho text overlays
- nhưng subtitle phải có shape riêng để upstream bật/tắt rõ ràng

### F-5. Contract phải phân biệt visual chính và overlay phụ

- layer visual bắt buộc phải được đánh dấu
- contract phải nói rõ missing asset thì làm gì

### F-6. Scene có visual bắt buộc bị thiếu asset không được degrade âm thầm

Hệ thống phải:

- emit structured warning/error
- áp dụng policy explicit
- tránh output "nền đen + subtitle/text" khi intent gốc là scene có visual chính

### F-7. Text-only scene vẫn phải được support

PRD này không cấm:

- text-only scene
- narration-only scene
- audio-only scene

Nhưng các scene đó phải là intentional, không phải hậu quả của việc visual chính bị mất.

## 8. Non-Functional Requirements

### N-1. Auditability

Từ request và result phải truy ra được:

- planned scene count
- actual rendered scene count
- scene nào bị missing visual
- scene nào có subtitle bật

### N-2. Backward compatibility

- `v1` requests vẫn được support trong giai đoạn migration
- `v2` là schema chuẩn mới
- behavior subtitle mặc định chỉ thay đổi với `v2`

### N-3. Observability

Tối thiểu cần có code hoặc log field cho:

- `planned_scene_count`
- `actual_scene_count`
- `required_visual_missing`
- `missing_asset_policy_applied`
- `subtitle_enabled`

### N-4. Testability

Phải có test cho:

- request `1, 3, 7, 10+` scenes
- request không bật subtitle
- request bật subtitle ở default level
- request bật subtitle override ở scene level
- missing `primary_visual`
- text-only scene intentional

## 9. Migration Requirements

## 9.1 Phase 1: Add V2 Schema And Dual Parsing

- thêm model/schema/docs/examples cho `v2`
- `maker8` parse được cả `v1` lẫn `v2`
- chưa bắt buộc upstream chuyển ngay

## 9.2 Phase 2: Upstream Emits Planning Metadata And Subtitle Defaults

- upstream phải phát `planning`
- upstream phải explicit `defaults.subtitles.enabled`
- default mới là `false`

## 9.3 Phase 3: Deprecate Subtitle-As-Text-Layer Pattern

- example và docs chính thức không còn dùng `subtitle-text` như default pattern
- `scene.subtitle` trở thành pattern chính thức

## 9.4 Phase 4: Enforce Required Visual Policy

- upstream phải đánh dấu `primary_visual`
- downstream phải enforce `missing_asset_policy`
- black background do missing required visual không còn là degrade mơ hồ

## 10. Acceptance Criteria

PRD này được xem là hoàn thành khi các điều kiện sau được đáp ứng:

1. Có schema `v2` mô tả rõ `planning`, `subtitle`, `role`, `required`, `missing_asset_policy`.
2. `maker8` có thể nhận request có số scene biến thiên mà không phụ thuộc assumption `5`.
3. Subtitle mặc định là off; request không bật subtitle thì output không có subtitle.
4. Không còn pattern chính thức nào encode subtitle mặc định bằng `text layer` generic.
5. Scene có `primary_visual` bị thiếu asset không còn âm thầm rơi về nền đen nếu policy không cho phép.
6. JSON schema, examples, fixtures, tests và docs field-level được cập nhật đồng bộ.
7. Result/logs cho phép audit:
   - planned scene count
   - required visual missing
   - subtitle enabled state

## 11. Recommended Ownership

| Area | Owner chính | Ghi chú |
|---|---|---|
| Quyết định `scene_count` | Upstream planner / `editor8` | Không phải trách nhiệm của renderer |
| Phát request `v2` đúng contract | Upstream producer | Phải explicit subtitle + planning metadata |
| Validate và render theo contract | `maker8` | Enforce visual policy, không tự thêm subtitle |
| Schema / examples / fixtures canonical | Shared contract owners | `render_contracts` là source of truth |

## 12. Kết luận

Vấn đề hiện tại không phải chỉ là một bug render đơn lẻ. Đây là vấn đề contract semantics giữa upstream và `maker8`.

Nếu chỉ "fix subtitle" hoặc chỉ "fix black background" mà không redesign request schema, hệ thống vẫn sẽ tiếp tục mơ hồ ở 3 điểm:

- ai quyết định số scene
- scene nào thực sự bắt buộc có visual
- subtitle có đang được bật chủ động hay chỉ bị encode lẫn vào text layer

Hướng đúng là nâng request contract sang `v2` với:

- planning metadata cho `scene_count`
- subtitle capability riêng, default off
- semantic role + required visual policy cho layers

Khi đó `maker8` mới có đủ thông tin để render đúng intent thay vì chỉ render theo primitive mơ hồ.
