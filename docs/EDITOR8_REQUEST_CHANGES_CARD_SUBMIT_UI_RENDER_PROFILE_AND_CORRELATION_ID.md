# Request Changes Card: Simplify `editor8` Submit UI And Expose Render Profile / Video Size

## ID

`EDITOR8-SUBMIT-UI-TRACE-RENDER-METADATA`

## Priority

`P2 - Medium`

## Tóm tắt

UI `editor8` hiện có một field `Correlation ID (optional)` cho manual submit, nhưng cùng lúc lại không cho user chọn hay nhìn thấy rõ:

- render profile
- video size
- fps

Qua review implementation hiện tại, đây là một UX/API mismatch:

- `Correlation ID` đang là input user-facing dù giá trị của nó chủ yếu phục vụ trace/search nội bộ
- render profile đã tồn tại trong contract/pipeline nhưng không được expose rõ ở submit flow
- frontend type và backend API cho `/api/submit` đang bị drift về `canvas_profile` / `dry_run`

Kết luận: request này nên bị `request changes`.

## Findings đã xác minh

### 1. `Correlation ID` là optional ở UI, nhưng không phải field “vô dụng” trong hệ thống

`Submit` page hiện render input:

- `../editor8/frontend/src/app/submit/page.tsx`
  - state `correlationId` tại dòng 26
  - submit payload gửi `correlation_id: correlationId || undefined` tại dòng 47
  - field input user-facing tại dòng 202-217

Backend cũng đang persist và dùng field này cho tracing/search:

- `../editor8/backend/src/editor8/api/routes.py`
  - `SubmitTextRequest.correlation_id` tại dòng 177
  - `Job.correlation_id = body.correlation_id` tại dòng 254
  - `EditorInputMessage.correlation_id = body.correlation_id` tại dòng 276
- `../editor8/backend/src/editor8/models/database.py`
  - cột `jobs.correlation_id` tại dòng 37
  - index `ix_jobs_correlation_id` tại dòng 64
- `../editor8/backend/src/editor8/pipeline/orchestrator.py`
  - propagate vào logging context tại dòng 78
- `../editor8/backend/src/editor8/utils/logging.py`
  - inject `correlation_id` vào structured logs tại dòng 27-29
- `../editor8/backend/src/render_contracts/render_spec.py`
  - `trace.correlation_id` tại dòng 230-231
- `../editor8/backend/src/editor8/kafka/__init__.py`
  - add Kafka header `correlation_id` tại dòng 98-99

Nói cách khác:

- không nên xóa semantic `correlation_id` khỏi backend/contract
- nhưng cũng không có lý do mạnh để buộc người dùng manual submit phải nhập tay field này

### 2. Với manual submit, `job_id` đã là UUID được generate server-side

`/api/submit` đang tạo:

- `job_id = str(uuid.uuid4())`
- tại `../editor8/backend/src/editor8/api/routes.py:243`

Điều này làm input `Correlation ID` ở manual UI trở nên thừa về mặt UX:

- user đang bị hỏi nhập thêm một identifier thứ hai
- identifier đó không quyết định output render
- trong flow manual submit nội bộ, `job_id` đã đủ để định danh request

Nếu vẫn cần `correlation_id` cho trace/search:

- nên auto-generate ở backend
- tốt nhất là reuse chính `job_id` làm `correlation_id` cho manual submit
- nếu đội muốn tách riêng semantic thì generate thêm UUID server-side, nhưng không nên hiển thị field này trên UI chính

### 3. Render profile đã có ở contract/pipeline, nhưng submit UI không expose

Contract input và render pipeline đã support:

- `EditorInputMessage.canvas_profile`
  - `../editor8/backend/src/editor8/models/contracts.py:23`
- `EditorInputMessage.dry_run`
  - `../editor8/backend/src/editor8/models/contracts.py:22`
- `RenderRequest.canvas_profile`
  - `../editor8/backend/src/render_contracts/render_spec.py:247`

Assembler còn map preset rất rõ:

- `short_vertical -> 1080x1920 @ 30fps`
- `horizontal -> 1920x1080 @ 30fps`
- tại `../editor8/backend/src/editor8/pipeline/assembler.py:38-40`
- fallback mặc định `short_vertical` tại dòng 77-79

Nhưng `Submit` UI hiện:

- không có selector cho `canvas_profile`
- không có chỗ hiển thị derived video size / fps
- không giải thích default render profile nào sẽ được dùng

### 4. Frontend type và backend `/api/submit` đang drift

Frontend type nói rằng manual submit hỗ trợ:

- `dry_run`
- `canvas_profile`
- `../editor8/frontend/src/types/index.ts:249-257`

Nhưng backend `/api/submit` model hiện chỉ nhận:

- `text_prompt`
- `lang`
- `style`
- `quick_approve`
- `constraints`
- `correlation_id`
- `../editor8/backend/src/editor8/api/routes.py:169-177`

Đây là drift thực sự ở interface:

- frontend type đã “hứa” nhiều hơn backend support
- nếu UI được sửa để cho chọn render profile mà API không đổi thì vẫn sai contract

### 5. UI hiện đang ưu tiên hiển thị `correlation_id` hơn render metadata

Hiện tại:

- jobs list có hẳn cột `Correlation`
  - `../editor8/frontend/src/app/jobs/page.tsx:239`
  - render cell tại dòng 289-292
- job detail header cũng show `corr: ...`
  - `../editor8/frontend/src/app/jobs/[id]/page.tsx:276-278`

Trong khi đó render metadata lại không được show như summary rõ ràng:

- `SceneEditor` chỉ show `canvas.w x canvas.h` và `fps`
  - `../editor8/frontend/src/components/editor/SceneEditor.tsx:91-109`
- nhưng thông tin này nằm sâu trong tab editor
- không show `canvas_profile`
- không hiện như metadata card ở phần đầu job

Tức là UI đang dành prime real estate cho một field debug/trace, trong khi bỏ qua field user-facing hơn là render profile và output size.

## Request Changes

### 1. Bỏ input `Correlation ID` khỏi manual submit UI

`Correlation ID` không nên còn là field editable mặc định trên `Submit` page.

Yêu cầu:

- remove block input tại `../editor8/frontend/src/app/submit/page.tsx:202-218`
- không yêu cầu user nhập tay identifier phục vụ trace nội bộ
- nếu cần debug, chỉ nên hiện ở advanced/debug section, không phải primary form

### 2. Vẫn giữ `correlation_id` trong backend/contract, nhưng auto-fill server-side

Không request xóa field này khỏi hệ thống.

Yêu cầu:

- nếu manual submit không truyền `correlation_id`, backend phải tự set giá trị non-empty
- khuyến nghị mạnh: `correlation_id = job_id` trong manual submit flow vì `job_id` đã là UUID
- vẫn phải persist vào `jobs.correlation_id`
- vẫn phải đi xuyên suốt vào:
  - `EditorInputMessage.correlation_id`
  - structured logs
  - `RenderRequest.trace.correlation_id`
  - Kafka header `correlation_id`

Lý do:

- giữ traceability/searchability hiện có
- bỏ burden UX không cần thiết
- tránh tạo thêm một identifier random thứ hai mà user không dùng tới

### 3. Align lại `/api/submit` với contract đã có

`SubmitTextRequest` backend phải được mở rộng để nhận ít nhất:

- `canvas_profile`
- và nếu team đã giữ trong frontend type thì cả `dry_run`

Ngoài việc nhận request, backend cũng phải:

- lưu các field này vào `jobs.input_request`
- đưa chúng vào `EditorInputMessage`
- giữ semantic mặc định rõ ràng khi field không được truyền

Không chấp nhận tiếp tục để frontend type và backend route lệch nhau.

### 4. Submit UI phải show rõ render profile và derived output size

Trên `/submit`, user phải nhìn thấy rõ trước khi bấm submit:

- render profile được chọn
- output size tương ứng
- fps tương ứng

Tối thiểu phải có 2 preset:

- `short_vertical` -> `1080x1920 @ 30fps`
- `horizontal` -> `1920x1080 @ 30fps`

UI không được để default “ngầm” trong backend mà không nói cho user biết.

### 5. Job detail UI phải show render metadata ở summary layer, không chỉ trong editor tab

Trên trang job detail, cần có summary card hoặc metadata chips gần header để show:

- `render profile`
- `video size`
- `fps`
- nếu có thì `dry_run`

Không đủ nếu thông tin này chỉ nằm trong `SceneEditor`:

- vì user phải mở đúng tab mới thấy
- và hiện tại còn thiếu `canvas_profile` label

### 6. Jobs list nên giảm ưu tiên của cột `Correlation`, tăng ưu tiên cho render metadata

Request thay đổi đề xuất:

- bỏ cột `Correlation` khỏi default jobs table
- hoặc chuyển nó vào tooltip / debug mode / job detail
- dùng chỗ đó để show render summary như:
  - `short_vertical · 1080x1920`
  - hoặc `horizontal · 1920x1080`

Nếu team chưa muốn đổi jobs list ngay, thì ít nhất:

- job detail phải có render summary rõ ràng
- submit form phải có preset selector + size preview

## Non-goals

- không yêu cầu xóa hoàn toàn `correlation_id` khỏi contract giữa các service
- không yêu cầu redesign toàn bộ render spec
- không yêu cầu bỏ trace/search theo `correlation_id` cho các producer ngoài `editor8` UI

## Acceptance Criteria

- `/submit` không còn field editable `Correlation ID` trong primary UI
- manual submit không truyền `correlation_id` vẫn tạo ra `jobs.correlation_id` non-empty
- giá trị `correlation_id` đó vẫn xuất hiện xuyên suốt ở DB, logs, `RenderRequest.trace`, và Kafka header
- backend `/api/submit` support `canvas_profile` và không còn drift với frontend type
- `/submit` hiển thị rõ preset render và derived video size/fps trước khi submit
- job detail hiển thị rõ `render profile` và `video size` ở summary layer, không bắt user đào vào editor tab
- nếu jobs list tiếp tục có giới hạn bề ngang, render metadata phải được ưu tiên hơn cột correlation mặc định
- có test hoặc contract check ngăn frontend/backend drift cho `SubmitTextRequest`

## Kết luận

Đây không phải là request “xóa hẳn correlation_id”.

Request đúng là:

- bỏ manual data entry không cần thiết
- giữ traceability ở backend bằng auto-generated value
- expose những thông tin thực sự quan trọng với user là render profile và video size
- sửa drift giữa UI type và API contract trước khi mở rộng tiếp submit flow
