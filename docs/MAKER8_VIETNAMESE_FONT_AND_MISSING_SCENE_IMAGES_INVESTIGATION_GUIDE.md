# Maker8 Investigation Guide: Vietnamese Font Errors And Missing Scene Images

## 1. Goal

Tài liệu này hướng dẫn investigate và đề xuất hướng fix cho hai nhóm lỗi đang thấy trên output video:

- text layer render lỗi tiếng Việt
- một số scene render ra nhưng thiếu image/video layer mong đợi

Mục tiêu không chỉ là fix triệu chứng ở một file, mà phải giữ behavior và contract consistent trên toàn project:

- runtime rendering
- pipeline degradation semantics
- canonical contract
- JSON schema
- examples / fixtures
- tests
- source-of-truth docs

---

## 2. Current Evidence From The Codebase

### 2.1 `font_ref` đang là contract nhưng chưa có runtime backing deterministic

- Canonical contract default `font_ref` là `font:inter:regular` trong [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py).
- README và example request cũng dùng `font:inter:*` trong [`README.md`](../README.md) và [`docs/examples/render_request.example.json`](./examples/render_request.example.json).
- Nhưng runtime text renderer map:
  - `font:inter:regular` -> `None`
  - `font:inter:bold` -> `None`
  trong [`src/maker8/rendering/text.py`](../src/maker8/rendering/text.py)
- Khi không resolve được font path thật, code fallback sang `ImageFont.load_default(size)` trong [`src/maker8/rendering/text.py`](../src/maker8/rendering/text.py).
- Repo hiện không ship bất kỳ file font `.ttf/.otf/.ttc` nào.
- [`Dockerfile`](../Dockerfile) cũng không cài package font hệ thống nào.

Kết luận:

- `font_ref` hiện mới đúng ở layer contract/docs, nhưng chưa được bind vào một font asset/runtime deterministic.
- Nếu video đang lỗi tiếng Việt, đây là suspect số 1.
- Ngay cả khi một số environment tình cờ render được tiếng Việt bằng Pillow default font, behavior đó vẫn không phải project contract ổn định.

### 2.2 Missing image/video layer hiện có thể bị drop silently

- [`src/maker8/rendering/layers.py`](../src/maker8/rendering/layers.py) trả `None` nếu `asset_ref` không tồn tại trong `asset_paths`:
  - `_build_video()`
  - `_build_image()`
- [`src/maker8/rendering/composer.py`](../src/maker8/rendering/composer.py) chỉ append clip nếu `lc is not None`, nên layer có thể biến mất mà không có log/warning cấp layer.
- [`src/maker8/pipeline/render.py`](../src/maker8/pipeline/render.py) chỉ warning khi cả scene không còn content hợp lệ và bị skip với `SCENE_NO_CONTENT`.

Kết luận:

- Nếu scene vẫn còn text layer hoặc còn một visual layer khác, scene vẫn render thành công nhưng image layer bị mất có thể không được report rõ ràng.
- Đây phù hợp với symptom "video đã tạo ra nhưng một số scene không có hình ảnh".

### 2.3 Có drift contract/example/runtime ngay trong repo

- README document `valign` là `"top" | "center" | "bottom"` trong [`README.md`](../README.md).
- Runtime text renderer chỉ xử lý `"center"` và `"bottom"`, giá trị khác sẽ rơi về top trong [`src/maker8/rendering/text.py`](../src/maker8/rendering/text.py).
- Nhưng golden fixture lại dùng `"middle"` trong [`tests/fixtures/golden_multiscene_request.json`](../tests/fixtures/golden_multiscene_request.json).

Kết luận:

- Project đang có ít nhất một ví dụ contract drift thật.
- Khi fix font/image issue, phải xử lý luôn nhóm inconsistency kiểu này, nếu không sẽ tiếp tục phát sinh bug "đúng schema nhưng sai behavior".

---

## 3. Investigation Principles

1. Không fix riêng renderer rồi bỏ quên contract/docs/tests.
2. Không kết luận lỗi font chỉ dựa trên video output; phải trace `font_ref -> actual font file/path -> runtime environment`.
3. Không kết luận scene thiếu ảnh chỉ từ stage `RENDER`; phải trace từ request payload qua `RESOLVE_ASSETS`, `DOWNLOAD`, `NORMALIZE`, rồi mới đến layer composition.
4. Mọi fix phải review cả degraded semantics:
   - video vẫn render được
   - nhưng phải surface được `PARTIAL` / `warnings[]` đúng mức chi tiết

---

## 4. What To Review

### 4.1 Rendering Runtime

| Component | File | Why review | What to verify |
|---|---|---|---|
| Text font loading | [`src/maker8/rendering/text.py`](../src/maker8/rendering/text.py) | Điểm quyết định font thật được dùng | `font_ref` nào được hỗ trợ, fallback order, có deterministic không, có log khi fallback không |
| Text layer creation | [`src/maker8/rendering/layers.py`](../src/maker8/rendering/layers.py) | Text layer đi qua đây trước khi thành `ImageClip` | `layer.style`, `text_align`, `valign`, `rect` có được áp dụng đúng không |
| Scene composition | [`src/maker8/rendering/composer.py`](../src/maker8/rendering/composer.py) | Layer `None` đang bị bỏ qua ở đây | Có observability đủ cho layer bị drop không |

### 4.2 Pipeline That Can Remove Visual Content

| Component | File | Why review | What to verify |
|---|---|---|---|
| Asset ref validation | [`src/maker8/pipeline/validate.py`](../src/maker8/pipeline/validate.py) | Chặn `asset_ref` không tồn tại từ sớm | Request có pass validate nhưng vẫn mất asset ở runtime không |
| Resolve stage | [`src/maker8/pipeline/resolve.py`](../src/maker8/pipeline/resolve.py) | Asset plan shape có thể sai ngay từ đầu | `asset.type`, `source.kind`, `expected_type`, filename plan |
| HTTP source connector | [`src/maker8/plugins/sources/http_source.py`](../src/maker8/plugins/sources/http_source.py) | Image assets thường đi qua đây | URL có extension đúng không, MIME guess có đủ tin cậy không |
| YouTube source connector | [`src/maker8/plugins/sources/youtube.py`](../src/maker8/plugins/sources/youtube.py) | Video source chính | Asset nào lẽ ra là image nhưng đang bị encode như video hay không |
| Download stage | [`src/maker8/pipeline/download.py`](../src/maker8/pipeline/download.py) | Asset có thể fail và bị degrade | `failed_assets`, `warnings`, `asset_report` |
| Normalize stage | [`src/maker8/pipeline/normalize.py`](../src/maker8/pipeline/normalize.py) | Images hiện bypass normalize | Có cần validate image integrity ở đây không |
| Render stage viability | [`src/maker8/pipeline/render.py`](../src/maker8/pipeline/render.py) | Scene có thể render dù thiếu một phần nội dung | Scene-level warning có đủ để debug layer-level missing không |
| Result emission | [`src/maker8/pipeline/emit.py`](../src/maker8/pipeline/emit.py) | Degradation phải đi ra ngoài | `warnings[]`, `PARTIAL`, `asset_report` có giúp downstream review được không |

### 4.3 Contract / Docs / Fixtures / Tests

| Component | File | Why review | What to verify |
|---|---|---|---|
| Canonical contract | [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py) | Nơi định nghĩa wire-format thật | `TextStyle.font_ref`, `Layer.valign`, allowed values, defaults |
| Maker8 re-export | [`src/maker8/models/spec.py`](../src/maker8/models/spec.py) | Phải đi cùng canonical model | Không edit lệch khỏi canonical |
| JSON schema | [`docs/schemas/render_request.schema.json`](./schemas/render_request.schema.json) | External contract visibility | Schema có phản ánh allowed values/font strategy mới không |
| README | [`README.md`](../README.md) | Public developer guidance | Bảng field docs có khớp runtime không |
| Specs doc | [`docs/maker8-specs.md`](./maker8-specs.md) | Long-form contract doc | Font/valign/examples có đồng bộ không |
| Source of truth | [`docs/MAKER8_SOURCE_OF_TRUTH.md`](./MAKER8_SOURCE_OF_TRUTH.md) | Operational truth table | Runtime behavior mới có được cập nhật không |
| Example request | [`docs/examples/render_request.example.json`](./examples/render_request.example.json) | Repro/example input | Font refs và text tiếng Việt có còn hợp lệ không |
| Golden fixtures | [`tests/fixtures/golden_multiscene_request.json`](../tests/fixtures/golden_multiscene_request.json) | Regression data | `valign`, `font_ref`, image layer semantics có đúng contract không |
| Contract tests | [`tests/test_contracts.py`](../tests/test_contracts.py) | Drift detection | Có assert cho font/valign/examples chưa |
| Survivability tests | [`tests/test_survivability.py`](../tests/test_survivability.py) | Missing asset degradation | Có cover missing layer nhưng scene vẫn render không |
| Runtime image/text tests | `tests/` | Cần bổ sung nếu chưa có | Có test render chữ tiếng Việt và layer asset missing không |

### 4.4 Runtime Packaging

| Component | File | Why review | What to verify |
|---|---|---|---|
| Python deps | [`pyproject.toml`](../pyproject.toml) | Pillow behavior phụ thuộc version/runtime | Version pin có đủ chặt để tránh drift không |
| Container image | [`Dockerfile`](../Dockerfile) | Runtime prod quyết định font availability | Font package/file nào thực sự có trong container |

---

## 5. Investigation Workflow

### Step 1: Capture The Exact Failed Input/Output Pair

Phải thu đủ 4 artefacts cho cùng một job:

1. `RenderRequest` input JSON thật
2. output video thật
3. `RenderResult` / `warnings[]` / `status`
4. logs theo `job_id`

Nếu thiếu một trong 4 artefacts này thì rất dễ fix nhầm layer symptom.

### Step 2: Build A Scene Review Matrix

Tạo một bảng cho từng scene:

| scene_id | layer_id | type | asset_ref | expected visual | actual visual | asset downloaded | asset normalized | warning/log seen |
|---|---|---|---|---|---|---|---|---|

Giải thích:

- Nếu `text` đúng nhưng `image/video` mất, bug nằm ở asset pipeline hoặc silent layer drop.
- Nếu text tiếng Việt sai ở mọi scene, bug nằm ở font resolution/runtime packaging.
- Nếu text chỉ sai ở một số scene, review `font_ref`, `style`, `rect`, `wrap`, `valign`, và actual text content từng scene.

### Step 3: Investigate Font Resolution End-To-End

Review tối thiểu các câu hỏi sau:

1. Request thực tế đang dùng các `font_ref` nào?
2. Mỗi `font_ref` có map tới font file thật hay không?
3. Font file đó có tồn tại trong repo/container không?
4. Nếu không tồn tại, runtime đang fallback sang gì?
5. Có log nào cho thấy fallback này không?
6. Kết quả có deterministic giữa local, CI, container production không?

Commands hữu ích:

```bash
rg -n '"font_ref"' docs/examples tests/fixtures src README.md
find . -type f \( -iname '*.ttf' -o -iname '*.otf' -o -iname '*.ttc' \) | sort
python - <<'PY'
from PIL import ImageFont
font = ImageFont.load_default(48)
print(type(font))
print(getattr(font, "path", None))
PY
```

Điều cần giải thích trong report:

- tại sao `font:inter:*` hiện không đại diện cho một font asset thật
- tại sao fallback hiện tại không đủ để đảm bảo Vietnamese rendering consistency
- tại sao fix phải là deterministic font resolution chứ không chỉ “thử font khác xem sao”

### Step 4: Investigate Missing Scene Images End-To-End

Trace mỗi `asset_ref` bị mất qua các điểm sau:

1. `RenderRequest` có khai báo asset không
2. `VALIDATE` có chặn ref sai không
3. `RESOLVE_ASSETS` tạo plan gì
4. `DOWNLOAD` có thành công không
5. `NORMALIZE` có đổi path/mark failed không
6. `asset_paths` cuối cùng trong `RENDER` có asset đó không
7. layer build có trả `None` không
8. scene có còn survive vì text layer hay layer khác không

Commands/log patterns nên review:

```bash
rg -n "resolve\\.asset|download\\.asset|normalize\\.asset|render\\.scene\\.skipped|emit\\." /path/to/job.log
rg -n "PARTIAL|warnings|asset_report" /path/to/result.json
```

Điều cần giải thích trong report:

- asset mất ở stage nào
- scene có bị skip hoàn toàn hay chỉ mất một layer
- tại sao output vẫn tạo được video dù visual content bị thiếu
- warning hiện tại có đủ để downstream/operator phát hiện không

### Step 5: Investigate Contract Drift

Review các drift sau, vì chúng làm bug khó đoán hơn:

- `font_ref` được document như thể có Inter, nhưng runtime không ship Inter
- `valign` documented là `center`, nhưng fixture dùng `middle`
- missing layer hiện là runtime behavior nhưng không được mô tả rõ trong docs/result semantics

Drift review phải trả lời:

1. source-of-truth thật nằm ở đâu
2. file nào đang stale
3. downstream nào đang tin vào file stale đó
4. fix nào là contract change, fix nào chỉ là implementation fix

---

## 6. Expected Root Causes To Validate

### Root Cause Candidate A: Font Alias Drift

Giải thích:

- contract/public examples quảng bá `font:inter:*`
- runtime không map alias này tới font file thật
- renderer dùng fallback font không được project quản lý

Expected evidence:

- không có font asset nào trong repo/container
- `text.py` fallback path luôn được hit cho `font:inter:*`
- output font khác thiết kế mong đợi, hoặc Vietnamese glyph rendering không ổn định

### Root Cause Candidate B: Silent Layer Drop

Giải thích:

- image/video layer có `asset_ref` nhưng asset không có mặt trong `asset_paths`
- `_build_image()` / `_build_video()` trả `None`
- composer bỏ qua clip `None` mà không emit warning cấp layer

Expected evidence:

- `ctx.warnings` có thể chỉ chứa `DOWNLOAD_FAILED` hoặc thậm chí không chỉ rõ layer nào biến mất
- scene vẫn render vì còn text layer hoặc background khác
- video output thiếu hình nhưng pipeline vẫn ra `PARTIAL` hoặc thậm chí thiếu signal đủ mạnh cho operator

### Root Cause Candidate C: Fixture / Contract Drift

Giải thích:

- examples/fixtures cho phép giá trị runtime không hỗ trợ nhất quán
- team debug theo docs/fixtures nhưng runtime xử lý khác

Expected evidence:

- fixture dùng `valign: "middle"`
- runtime không biết `"middle"`
- tests hiện chưa fail cho drift này

---

## 7. Solution Proposals

### 7.1 Font Fix: Make Font Resolution Deterministic

Đề xuất:

1. Chốt font production mặc định là Google `Roboto`.
   - weights tối thiểu nên bundle:
     - `Roboto-Regular.ttf`
     - `Roboto-Bold.ttf`
   - optional:
     - `Roboto-Italic.ttf`
     - `Roboto-BoldItalic.ttf`
   - lý do:
     - hỗ trợ tiếng Việt tốt
     - dễ đọc trên video dọc và ngang
     - hình dáng trung tính, phù hợp subtitle/title/overlay
     - phổ biến và dễ maintain
2. Nếu muốn coverage ngôn ngữ rộng hơn trong tương lai, đề xuất `Noto Sans` làm secondary fallback.
   - `Roboto` nên là visual default
   - `Noto Sans` chỉ nên dùng khi cần coverage rộng hơn Vietnamese/Latin
3. Chọn một strategy duy nhất cho `font_ref`: download font một lần, commit/bundle cùng hệ thống, không phụ thuộc font mặc định của OS.
4. Tạo registry alias rõ ràng:
   - `font:roboto:regular`
   - `font:roboto:bold`
   - optional fallback:
     - `font:noto_sans:regular`
     - `font:noto_sans:bold`
5. Để maintain backward compatibility, trong giai đoạn chuyển đổi nên xử lý:
   - `font:inter:regular` -> map tạm sang `font:roboto:regular`
   - `font:inter:bold` -> map tạm sang `font:roboto:bold`
   - nhưng docs/examples/schema mới nên chuyển sang `font:roboto:*` để tránh tiếp tục drift
6. Khi `font_ref` không resolve được:
   - log warning rõ ràng
   - có metric/counter
   - optional fail-fast ở startup nếu font alias bắt buộc bị thiếu
7. Add Vietnamese render regression tests với sample strings:
   - `Tiếng Việt`
   - `Đặng`
   - `Trường Sa`
   - `ắằẳẵặ`

#### 7.1.1 Recommended Packaging Strategy

Đề xuất packaging cụ thể:

1. Tạo thư mục font nằm trong package, ví dụ:
   - `src/maker8/assets/fonts/google/roboto/Roboto-Regular.ttf`
   - `src/maker8/assets/fonts/google/roboto/Roboto-Bold.ttf`
2. Lưu kèm metadata nguồn font:
   - upstream source URL
   - version/tag đã download
   - license note
   - optional checksum SHA256
3. Update build packaging để các font file được đóng vào wheel.
4. Runtime chỉ load font từ packaged path hoặc explicit absolute path đã biết.
5. Container không nên đi tải font lúc startup.
   - ideal path là `pip install` package xong đã có sẵn font files
   - nếu vẫn cần copy riêng trong image thì phải copy từ repo/package vào image lúc build, không tải ở runtime
6. Nếu dùng fontconfig/system font cache thì có thể refresh trong image build.
   - nhưng với Pillow, load trực tiếp từ packaged file path thường đơn giản và deterministic hơn

#### 7.1.2 Investigation Questions For Roboto Adoption

Khi chọn `Roboto`, report cần trả lời thêm:

1. Alias cũ `font:inter:*` sẽ deprecate hay remap vĩnh viễn?
2. Font files sẽ được đặt ở repo path nào để package và Docker cùng dùng một source?
3. Có cần thêm weight nào ngoài `regular` và `bold` cho title card hay không?
4. Có cần `Noto Sans` fallback cho non-Vietnamese scenes hay không?
5. Build config nào sẽ chịu trách nhiệm đảm bảo font files đi vào artifact phát hành?

Tại sao solution này đúng:

- biến `font_ref` từ semantic alias thành deterministic runtime contract
- loại bỏ behavior phụ thuộc environment/Pillow default internals
- giữ UI/design consistency giữa local, CI và production
- cho phép dùng một font production rõ ràng như `Roboto` mà không phải trông chờ host/container có sẵn font tương ứng

### 7.2 Missing Image Fix: Stop Dropping Layers Silently

Đề xuất:

1. Emit warning/log cấp layer khi image/video layer không build được:
   - `scene_id`
   - `layer_id`
   - `asset_ref`
   - asset stage status
2. Distinguish 2 cases:
   - `scene_skipped`
   - `scene_rendered_with_missing_layers`
3. Cân nhắc bổ sung warning code mới, ví dụ:
   - `LAYER_ASSET_MISSING`
   - `LAYER_RENDER_SKIPPED`
4. Nếu business muốn output nhìn đủ khung hơn:
   - render placeholder background cho scene mất asset
   - hoặc fallback text/solid background rõ ràng thay vì để scene trống
5. Validate image assets sớm hơn:
   - mở file bằng Pillow sau download
   - reject/mark failed nếu file không phải image hợp lệ

Tại sao solution này đúng:

- bug “scene có video nhưng thiếu hình” sẽ chuyển từ silent degradation thành diagnosable degradation
- operator và downstream sẽ biết thiếu layer nào, không chỉ biết job bị `PARTIAL`

### 7.3 Consistency Fix: Update All Project Surfaces In One Change

Khi implement fix, bắt buộc review và update đồng bộ:

1. [`src/render_contracts/render_spec.py`](../src/render_contracts/render_spec.py)
2. [`src/maker8/models/spec.py`](../src/maker8/models/spec.py)
3. [`src/maker8/rendering/text.py`](../src/maker8/rendering/text.py)
4. [`src/maker8/rendering/layers.py`](../src/maker8/rendering/layers.py)
5. [`src/maker8/rendering/composer.py`](../src/maker8/rendering/composer.py)
6. [`src/maker8/pipeline/render.py`](../src/maker8/pipeline/render.py)
7. [`src/maker8/pipeline/emit.py`](../src/maker8/pipeline/emit.py)
8. [`Dockerfile`](../Dockerfile)
9. [`README.md`](../README.md)
10. [`docs/maker8-specs.md`](./maker8-specs.md)
11. [`docs/MAKER8_SOURCE_OF_TRUTH.md`](./MAKER8_SOURCE_OF_TRUTH.md)
12. [`docs/schemas/render_request.schema.json`](./schemas/render_request.schema.json)
13. [`docs/examples/render_request.example.json`](./examples/render_request.example.json)
14. [`tests/fixtures/golden_multiscene_request.json`](../tests/fixtures/golden_multiscene_request.json)
15. [`tests/test_contracts.py`](../tests/test_contracts.py)
16. [`tests/test_survivability.py`](../tests/test_survivability.py)
17. packaged font directory mới, ví dụ `src/maker8/assets/fonts/`
18. build packaging config trong [`pyproject.toml`](../pyproject.toml)

Quy tắc:

- không update `maker8` runtime mà bỏ schema/examples cũ
- không đổi contract enum/value mà quên fixtures/tests
- không thêm warning semantics mới mà quên `RenderResult` review path
- không chọn `Roboto` hay font khác cho docs nhưng quên bundle font files vào artifact phát hành

---

## 8. Minimum Acceptance Criteria For The Real Fix

Fix chỉ được xem là complete khi đạt đủ:

1. `font_ref` resolve được thành font asset deterministic trong runtime production.
2. Font production được chọn rõ ràng.
   - khuyến nghị hiện tại: `Roboto`
   - secondary fallback khi cần coverage rộng hơn: `Noto Sans`
3. Font files đã được download, bundle vào package/image, và không phụ thuộc network hay host font ở runtime.
4. Ít nhất một automated test render text tiếng Việt đi qua pipeline text renderer.
5. Missing image/video layer không còn silent; phải có log hoặc `warnings[]` đủ chi tiết để xác định layer bị mất.
6. `README`, schema, examples, fixtures, tests không còn drift với runtime.
7. Các giá trị contract như `valign` được chuẩn hóa; không còn `"middle"` nếu runtime chỉ hỗ trợ `"center"`.
8. Cùng một input request phải cho behavior ổn định giữa local và container.

---

## 9. Recommended Implementation Order

1. Fix deterministic font mapping và packaging trước.
2. Chọn `Roboto` làm bundled production font, quyết định strategy remap/deprecate cho `font:inter:*`.
3. Add layer-level observability cho missing assets.
4. Chuẩn hóa contract drift như `valign`.
5. Update schema/docs/examples/fixtures/tests trong cùng PR.
6. Chạy regression review trên một request có tiếng Việt và một request có image layer bị fail.

---

## 10. Suggested Final Deliverables Of The Investigation

Investigation report cuối cùng nên có:

1. Root cause đã được chứng minh cho lỗi font tiếng Việt.
2. Root cause đã được chứng minh cho từng scene bị thiếu image/video.
3. Mapping `scene_id -> affected layer -> exact failure stage`.
4. Danh sách file cần sửa.
5. Danh sách file cần update để giữ consistency toàn project.
6. Quyết định font production cuối cùng:
   - `Roboto` là default hay không
   - có dùng `Noto Sans` làm fallback hay không
   - alias cũ `font:inter:*` sẽ remap hay deprecate
7. Acceptance criteria và regression tests sẽ thêm.

Nếu report cuối cùng không chỉ ra được:

- font alias nào map sang font file nào
- layer nào bị drop ở stage nào
- file docs/schema/tests nào cần sync

thì investigation vẫn chưa đủ sâu để fix an toàn.
