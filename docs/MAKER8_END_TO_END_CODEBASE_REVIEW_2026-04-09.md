# Maker8 End-to-End Codebase Review

## Scope

Review này tập trung vào:

- luồng end-to-end từ Kafka input đến result/DLQ
- code thừa và logic thừa
- mức độ hợp lý của cấu trúc thư mục hiện tại

Review dựa trên code hiện có trong repo và một lớp test trọng tâm:

```bash
./venv/bin/pytest -q tests/test_contracts.py tests/test_v2_contract.py tests/test_variable_scenes.py
```

Kết quả: `89 passed`.

## Findings

### 1. High: invalid JSON bị commit rồi biến mất, không có DLQ/result

Evidence:

- [`src/maker8/kafka/consumer.py`](../src/maker8/kafka/consumer.py) `:107-146` catch `json.JSONDecodeError`, chỉ log `consumer.invalid_json`, sau đó vẫn đi vào block `commit`.
- [`src/maker8/pipeline/orchestrator.py`](../src/maker8/pipeline/orchestrator.py) `:97-104` chỉ gửi invalid-payload DLQ khi payload đã parse thành `dict` nhưng fail `RenderRequest.model_validate()`.

Impact:

- poison message ở mức JSON syntax error bị acknowledge và mất khỏi queue
- operator không nhận được `RenderResult` hay `DLQPayload`
- đây là gap forensic nghiêm trọng vì message “mất dấu” khỏi hệ thống

Recommendation:

- consumer phải route invalid JSON sang cùng cơ chế DLQ thay vì chỉ log rồi commit
- thêm test riêng cho path `invalid JSON -> DLQ`

### 2. High: startup validation của TTS mâu thuẫn với Google ADC fallback và với preset-based provider selection

Evidence:

- [`src/maker8/services/tts_client.py`](../src/maker8/services/tts_client.py) `:147-149` và `:235-236` cho thấy Google provider hỗ trợ fallback sang ADC khi không có `credentials_path`.
- [`src/maker8/services/tts_client.py`](../src/maker8/services/tts_client.py) `:333-345` cũng nói rõ key ring có thể thiếu và provider sẽ fallback ADC.
- Nhưng [`src/maker8/services/tts_client.py`](../src/maker8/services/tts_client.py) `:412-422` coi `google_cloud` là available chỉ khi có `_google_ring`.
- [`src/maker8/app.py`](../src/maker8/app.py) `:118-125` kill process nếu `tts_service.has_provider()` trả `False`.
- Ngoài ra [`src/maker8/services/tts_client.py`](../src/maker8/services/tts_client.py) `:463-467` chọn provider theo preset ở runtime, nhưng startup check lại chỉ nhìn `self._default_provider`.

Impact:

- worker có thể refuse startup dù ADC hợp lệ
- worker cũng có thể fail startup vì default provider, dù preset thực tế của request có thể dùng provider khác
- bootstrap semantics và runtime semantics không khớp nhau

Recommendation:

- `has_provider()` phải phản ánh đúng runtime capability thực tế
- hoặc bỏ startup hard-fail này, chuyển sang per-request/per-scene validation rõ ràng hơn
- nếu vẫn giữ startup gate, phải support ADC đúng nghĩa và scan preset/provider strategy nhất quán

### 3. High: root `RenderRequest.spec_version` hiện gần như là field trang trí

Evidence:

- [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) `:288-299` khai báo cả `RenderRequest.spec_version` lẫn `render_spec.spec_version`.
- [`src/maker8/pipeline/validate.py`](../src/maker8/pipeline/validate.py) `:49-56` chỉ validate `ctx.render_spec.spec_version`.
- [`src/maker8/pipeline/validate.py`](../src/maker8/pipeline/validate.py) `:144-146` cũng chỉ gate V2 behavior theo `spec.spec_version == "2.0"`.

Impact:

- root version và nested version có thể lệch nhau mà runtime không bắt
- routing/versioning boundary của contract bị mơ hồ
- về lâu dài đây là nguồn bug khi team muốn version Kafka envelope và render spec độc lập

Recommendation:

- hoặc enforce `request.spec_version == render_spec.spec_version`
- hoặc bỏ một trong hai field để contract versioning có đúng một source of truth

### 4. Medium: missing-asset policy bị split thành 2 lớp logic và tạo warning trùng

Evidence:

- [`src/maker8/pipeline/render.py`](../src/maker8/pipeline/render.py) `:48-49` và `:180-237` áp dụng `missing_asset_policy` trước khi compose, đồng thời append warning `MISSING_ASSET_POLICY_APPLIED`.
- [`src/maker8/rendering/composer.py`](../src/maker8/rendering/composer.py) `:620-657` lại append thêm warning `LAYER_ASSET_MISSING` và xử lý `scene_placeholder`.

Impact:

- một missing required visual có thể tạo nhiều warning cho cùng một sự kiện
- business policy bị tách giữa stage layer và rendering layer
- khó giữ semantics ổn định khi sau này thêm policy mới

Recommendation:

- gom policy resolution vào một nơi duy nhất
- chỉ để composer render theo quyết định đã được resolve từ trước

### 5. Medium: runtime binary resolution không nhất quán, metadata có thể lệch binary thật đang dùng

Evidence:

- [`src/maker8/rendering/encoder.py`](../src/maker8/rendering/encoder.py) và normalize/render paths dùng binary resolve tập trung.
- Nhưng [`src/maker8/services/tts_client.py`](../src/maker8/services/tts_client.py) `:81-111` gọi literal `"ffprobe"`.
- [`src/maker8/utils/versions.py`](../src/maker8/utils/versions.py) `:29-49` gọi literal `"ffmpeg"` và default `"yt-dlp"`.
- Các caller của `collect_engine_versions()` trong [`src/maker8/pipeline/emit.py`](../src/maker8/pipeline/emit.py) `:82-95`, [`src/maker8/pipeline/upload.py`](../src/maker8/pipeline/upload.py) `:178-194`, [`src/maker8/pipeline/orchestrator.py`](../src/maker8/pipeline/orchestrator.py) `:323-353` đều không pass resolved path.

Impact:

- output metadata có thể report version của binary trên `PATH`, không phải binary managed/runtime thật
- behavior giữa startup probe và later metadata probe có thể lệch nhau

Recommendation:

- unify toàn bộ `ffmpeg`/`ffprobe`/`yt-dlp` access qua runtime resolver duy nhất

### 6. Medium: contract surface area lớn hơn implementation thật, tạo nhiều field “orphan/reserved”

Evidence:

- [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) `:39-42` có `SceneTiming.duration_mode`, nhưng [`src/maker8/rendering/composer.py`](../src/maker8/rendering/composer.py) `:603-610` hard-code duration selection theo `scene.duration -> tts -> 5.0`.
- [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) `:123-126` có `Layer.align`, nhưng [`src/maker8/rendering/layers.py`](../src/maker8/rendering/layers.py) `:111-166` không đọc field này.
- [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) `:202-208` có `PublishTarget.variant`, nhưng code search trong `src/maker8/` không có consumer logic cho field này.
- [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) `:282-285` có `ResultDestination.type`, nhưng runtime chỉ dùng `topic` và `key`.
- [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) `:294-297` có `publish_intent` và `planning`; runtime chỉ validate `planning.planned_scene_count` ở [`src/maker8/pipeline/validate.py`](../src/maker8/pipeline/validate.py) `:165-175`, còn `publish_intent` không được consume.

Impact:

- contract nhìn “giàu” hơn behavior thật
- integrator dễ hiểu nhầm rằng những field này có engine semantics
- maintenance cost tăng vì mỗi field phải được giữ sync trong schema/docs/examples/tests

Recommendation:

- phân loại rõ field nào là `ACTIVE`, `PASS_THROUGH`, `RESERVED`
- remove hoặc deprecate các field không có owner/runtime behavior rõ ràng

### 7. Medium: result emission/routing bị duplicate ở 2 code path

Evidence:

- [`src/maker8/pipeline/emit.py`](../src/maker8/pipeline/emit.py) `:27-49` resolve topic/key và build success result.
- [`src/maker8/pipeline/orchestrator.py`](../src/maker8/pipeline/orchestrator.py) `:320-355` lặp lại logic build failed result và resolve topic/key.

Impact:

- dễ drift giữa success path và failure path
- nếu routing/result schema thay đổi, phải sửa ở nhiều chỗ

Recommendation:

- trích ra một `ResultEmitter` hoặc một factory thống nhất cho `RenderResult` + topic/key resolution

### 8. Low: `WorkerState` có field quan sát chi tiết nhưng runtime không bao giờ set

Evidence:

- [`src/maker8/observability/state.py`](../src/maker8/observability/state.py) `:40-41` khai báo `current_asset_id`, `current_scene_id`.
- [`src/maker8/observability/state.py`](../src/maker8/observability/state.py) `:139-145` expose chúng ra snapshot.
- Nhưng code search trong `src/` không có chỗ nào set hai field này ngoài việc reset về `None`.

Impact:

- status file hứa hẹn granularity mà runtime không cung cấp
- observability surface bị inflated

Recommendation:

- hoặc set chúng thật trong các stage asset/scene-heavy
- hoặc bỏ khỏi state model và snapshot

## Code Thừa Và Logic Thừa

### Code thừa / future-only hiện thấy rõ

- `PublishStage` và `PublishStatus` trong [`src/maker8/models/common.py`](../src/maker8/models/common.py) `:49-66` hiện không có usage trong `src/` hay `tests/`.
- `maker8.models.spec` gần như là lớp re-export thuần của [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py): xem [`src/maker8/models/spec.py`](../src/maker8/models/spec.py) `:1-70`.
- `maker8.models.__init__` lại re-export thêm một vòng nữa: [`src/maker8/models/__init__.py`](../src/maker8/models/__init__.py) `:1-46`.

Nhận định:

- compatibility shim là hợp lý trong một giai đoạn migration
- nhưng hiện repo đang có quá nhiều cửa vào cho cùng một loại model, làm navigation và ownership khó hơn mức cần thiết

### Logic thừa / phân tán

- policy missing visual bị tách giữa `RenderStageImpl` và `composer`
- result building/routing bị tách giữa `EmitResultStage` và `Orchestrator._send_failed_result`
- versioning bị tách giữa root request và nested render spec nhưng không có invariant thống nhất

## Đánh Giá Cấu Trúc Thư Mục

### Điểm hợp lý

- `src/maker8/pipeline/`: chia stage theo luồng xử lý, dễ trace end-to-end.
- `src/maker8/rendering/`: gom concern MoviePy/FFmpeg/Pillow khá rõ.
- `src/maker8/plugins/`: tách source connectors và effects hợp lý.
- `src/maker8/services/`: chứa dependency wrappers tương đối đúng vai trò.

### Điểm chưa hợp lý

#### 1. Boundary models/contracts đang dày lớp

Hiện tồn tại đồng thời:

- `src/render_contracts/`
- `src/maker8/models/spec.py`
- `src/maker8/models/contracts.py`
- `src/maker8/models/common.py`
- `src/maker8/models/__init__.py`

Nhận định:

- nếu `render_contracts` thật sự là canonical package, thì phần lớn wire-format model nên được import trực tiếp từ đó
- `maker8.models.*` nên chỉ giữ lại phần maker8-specific như `RenderResult`, `DLQPayload`, `Manifest`, `OutputMeta`, `AssetWarning`

#### 2. `docs/` đang trộn canonical docs với review/investigation docs

Ở root `docs/` hiện đang có cả:

- canonical doc như `ARCHITECTURE.md`, `OPERATIONS_RUNBOOK.md`
- long-form spec như `maker8-specs.md`
- nhiều dated review / investigation / PRD files

Nhận định:

- dù [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md) `:3-4` tự nhận là canonical, overall docs tree vẫn khiến source of truth khó nhận biết
- nên tách ít nhất thành:
  - `docs/canonical/`
  - `docs/reviews/`
  - `docs/guides/`
  - giữ `docs/examples/` và `docs/schemas/` như hiện tại

## Testing Gaps

Những phần đáng chú ý nhưng hiện chưa thấy coverage tương xứng:

- consumer path cho `invalid JSON`
- startup bootstrap cho TTS provider availability, đặc biệt Google ADC
- metadata version collection với custom binary path
- actual end-to-end path cho `missing_asset_policy` trong render, thay vì chỉ contract validation

## Architectural Requirement: Solid But Simple

Mục tiêu kiến trúc tiếp theo không nên là thêm nhiều abstraction hơn. Mục tiêu đúng là làm hệ thống **solid nhưng simple**.

### Yêu cầu kiến trúc

1. Mỗi responsibility quan trọng chỉ nên có một owner rõ ràng.
   Ví dụ:
   - request/wire contract: `render_contracts`
   - pipeline control flow: `orchestrator`
   - render policy resolution: một layer duy nhất, không split giữa stage và composer
   - result emission/routing: một component/factory duy nhất

2. Mỗi khái niệm quan trọng chỉ nên có một source of truth.
   Không nên duy trì nhiều lớp re-export và nhiều field versioning nếu runtime không enforce invariant của chúng.

3. Runtime path phải ưu tiên đơn giản hơn “future flexibility”.
   Repo này hiện là một synchronous render worker. Các abstraction cho future publisher flow hoặc reserved contract fields chỉ nên giữ lại nếu đang tạo giá trị thực tế.

4. Contract surface phải nhỏ hơn hoặc bằng behavior thật của system.
   Không nên tiếp tục thêm field mới vào schema nếu chưa có:
   - owner
   - runtime semantics
   - observability
   - test coverage

5. Policy phải được resolve trước khi vào low-level rendering.
   Composer nên chủ yếu render theo intent đã được chốt, không tự mang thêm business policy song song với pipeline stage.

6. Failure semantics phải nhất quán từ đầu vào đến đầu ra.
   Mọi failure class quan trọng phải có một trong hai kết cục rõ ràng:
   - `RenderResult`
   - `DLQPayload`
   Không được tồn tại failure path chỉ log rồi mất dấu.

7. Documentation structure cũng phải simple.
   Canonical docs, review docs, investigation guides, examples và schemas phải được tách lớp rõ để người mới không phải đoán đâu là tài liệu authoritative.

### Định hướng refactor ở mức kiến trúc

- Giữ pipeline tuyến tính hiện tại; không cần tăng số layer kỹ thuật nếu chưa có concurrency model mới.
- Giảm số “pass-through field” và “reserved field” ở contract công khai.
- Giảm duplication giữa:
  - success result path và failed result path
  - missing-asset policy stage-level và render-level
  - binary resolution path và version-report path
- Chỉ giữ compatibility shim nào còn phục vụ migration thực tế; các shim còn lại nên được sunset có kế hoạch.

### Definition of Done cho hướng “solid nhưng simple”

Một thay đổi kiến trúc chỉ được xem là đạt nếu đồng thời thỏa các điều kiện sau:

- ít entry point hơn cho cùng một concern
- ít chỗ phải sửa hơn khi đổi một behavior
- invariant được enforce bằng code, không chỉ bằng doc
- operator nhìn log/result/DLQ là hiểu chuyện gì đã xảy ra
- số abstraction không tăng nếu không làm giảm coupling hoặc drift thật sự
- một dev mới có thể trace request từ Kafka input tới result/DLQ qua vài file chính, không cần đi qua nhiều lớp alias/re-export

## Tổng Kết

Codebase hiện có một runtime spine khá rõ: `pipeline/`, `rendering/`, `plugins/`, `services/` được tách tương đối hợp lý và contract tests v1/v2 đang tốt.

Debt chính không nằm ở chỗ “thiếu abstraction”, mà ở chỗ:

1. contract surface mở rộng nhanh hơn behavior thật
2. một số logic quan trọng bị split giữa nhiều layer
3. compatibility shims và future-only types đã bắt đầu làm ownership mờ đi
4. một vài lỗi boundary-level vẫn còn, đặc biệt invalid JSON handling và TTS startup semantics

Nếu cần ưu tiên xử lý, thứ tự nên là:

1. fix invalid JSON -> DLQ/result path
2. fix TTS provider startup semantics
3. thống nhất versioning invariant cho request/spec
4. gom policy missing-asset và result emission vào một chỗ
5. dọn contract/model surface: bỏ hoặc gắn nhãn rõ các field reserved/pass-through
