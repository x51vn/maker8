# Request Card: Investigate và fix lỗi prompt templating trên toàn project, đảm bảo consistency

## ID

`EDITOR8-PROMPTS-TEMPLATE-CONSISTENCY`

## Priority

`P1 - High`

## Tóm tắt vấn đề

Agent `QA_CRITIC` đang fail với lỗi:

```text
KeyError: Input to ChatPromptTemplate is missing variables {'\n  "title"'} ...
```

Đây không phải lỗi model output, mà là lỗi dựng prompt trước khi model thực thi.

## Triệu chứng quan sát được

- `QA_CRITIC` fail ngay khi gọi `chain.ainvoke(...)`
- log cho thấy lỗi phát sinh trong `ChatPromptTemplate._validate_input`
- agent retry nhưng fail y hệt
- pipeline fallback sang:
  - `QA_CRITIC failed, returning auto-approved review`
- hệ thống tiếp tục chạy nhưng bỏ qua review thực sự

## Root cause kỹ thuật

### 1. Hệ thống hiện có hai lớp templating chồng lên nhau

Luồng hiện tại:

1. `PromptManager.render()` render prompt bằng regex thay `{var}` trong chuỗi
2. `create_agent()` đưa `system_prompt` đã render vào `ChatPromptTemplate.from_messages(...)`
3. LangChain lại parse tiếp dấu `{}` trong `system_prompt`

Đây là inconsistent behavior giữa hai hệ templating:

- `PromptManager.render()` coi chuỗi đã render là final text
- `ChatPromptTemplate` lại coi text đó vẫn là template

Khi giá trị variable chứa raw JSON hoặc text có dấu `{}`, LangChain sẽ hiểu nhầm đó là placeholder mới.

### 2. `QA_CRITIC` chèn raw JSON vào `system_prompt`

Hiện `QA_CRITIC` seed prompt có:

- `system_prompt` chứa `{blueprint}`
- `user_prompt_template` cũng chứa `{blueprint}`

Trong runtime:

- `run_qa_critic()` serialize blueprint thành JSON text
- JSON text này được nhét vào `variables["blueprint"]`
- `PromptManager.render()` thay `{blueprint}` bằng raw JSON trong `system_prompt`
- JSON có các key như `{ "title": ... }`
- LangChain parse lại và coi `{\n  "title"}` là missing template variable

=> agent fail trước cả khi model được gọi đúng cách.

## Vì sao đây không phải lỗi riêng của `QA_CRITIC`

`QA_CRITIC` chỉ là nơi lộ lỗi đầu tiên rõ nhất. Lớp lỗi này có thể tái diễn ở các prompt khác nếu `system_prompt` nhận dữ liệu giàu cấu trúc hoặc text không brace-safe.

### Các điểm có nguy cơ tương tự trong code hiện tại

#### A. `QA_CRITIC`

- chèn `blueprint_json` vào `system_prompt`
- đây là case đã fail thực tế

#### B. `STORY_ARCHITECT`

- `system_prompt` dùng `{research_summary}`
- `research_summary` là output free-form từ agent khác
- nếu text này chứa `{}` hoặc code/JSON fragment, có thể gây lỗi tương tự

#### C. `SCENE_SPLITTER`

- `system_prompt` dùng `{constraints_hint}`
- `constraints_hint` hiện được dựng từ `constraints` dict
- string hóa dict Python/JSON có thể chứa `{}` trực tiếp
- đây là risk cùng lớp với `QA_CRITIC`

#### D. Bất kỳ prompt nào inject raw rich text vào `system_prompt`

Rule hiện tại chưa rõ ràng, nên cùng pattern có thể tái xuất hiện ở:

- prompt mới trong tương lai
- prompt được sửa thủ công qua prompt admin/API
- prompt version mới do team tạo

## Vấn đề consistency across the project

Đây không chỉ là bug prompt riêng lẻ. Đây là vấn đề consistency của toàn bộ prompt system:

- có 2 tầng template semantics khác nhau
- không có rule chung về dữ liệu nào được phép vào `system_prompt`
- không có guardrail ngăn raw JSON / raw braces đi vào `system_prompt`
- không có test regression cho lớp lỗi này

Nếu không chuẩn hóa, mỗi prompt mới đều có thể vô tình tái tạo bug tương tự.

## Yêu cầu investigate

### 1. Audit toàn bộ prompt templates

Rà soát toàn bộ seed prompts và prompt versions đang active để tìm:

- `system_prompt` có inject dữ liệu rich text
- `system_prompt` có inject JSON-like text
- `system_prompt` có inject dữ liệu user-provided hoặc agent-generated không brace-safe
- prompt nào đang duplicate cùng dữ liệu ở cả `system_prompt` và `user_prompt_template`

### 2. Audit toàn bộ nơi truyền variables vào agent

Rà soát tất cả call sites của `run_with_quality_retry()` / `run()` để phân loại:

- biến nào là scalar an toàn
- biến nào là free text
- biến nào là JSON / dict / list serialized
- biến nào đang được đưa vào `system_prompt`

### 3. Audit runtime prompt catalog

Không chỉ audit seed file trong repo.

Cần kiểm tra cả:

- active prompt versions trong DB
- prompt chỉnh tay qua API/admin
- prompt versions mới có thể đã được tạo ngoài seed file

## Yêu cầu fix triệt để

### 1. Thiết lập rule nhất quán cho prompt architecture

Rule bắt buộc trên toàn project:

- `system_prompt` chỉ được chứa:
  - instruction ổn định
  - metadata scalar, brace-safe
  - không chứa raw JSON / raw free text / raw generated content
- mọi dữ liệu lớn, dynamic, rich text, JSON, code block phải đi qua `human/input` message

Nói ngắn gọn:

- instructions ở `system`
- payload ở `human`

### 2. Fix `QA_CRITIC`

Tối thiểu cần:

- bỏ việc inject `{blueprint}` vào `system_prompt`
- chỉ truyền blueprint JSON qua `user_prompt_template` hoặc input message

Không được giữ cùng payload ở cả `system_prompt` lẫn `user_prompt_template`.

### 3. Loại bỏ hoặc kiểm soát double templating inconsistency

Chọn một hướng rõ ràng và áp dụng nhất quán:

- hoặc đảm bảo mọi giá trị render vào `system_prompt` đều được escape brace an toàn trước khi vào LangChain
- hoặc không render rich/dynamic payload vào `system_prompt` nữa
- hoặc refactor agent factory/prompt rendering để chỉ còn một semantics template rõ ràng

Nhưng không được giữ trạng thái “PromptManager render xong rồi LangChain lại parse tiếp theo cách khác” mà không có guardrails.

### 4. Thêm validation cho prompt definitions

Cần có validation rule khi seed/create/update/activate prompt:

- reject hoặc cảnh báo mạnh nếu `system_prompt` chứa placeholder thuộc nhóm rich content
- reject hoặc cảnh báo nếu một placeholder JSON payload vừa nằm ở `system_prompt` vừa nằm ở `user_prompt_template`
- validate prompt template tương thích với runtime prompt engine

### 5. Thêm runtime-safe error handling

Nếu prompt vẫn bị misconfigured, hệ thống không nên im lặng fallback mà không có visibility.

Cần:

- log rõ nguyên nhân là prompt templating/configuration error
- surface metric hoặc health signal cho prompt misconfiguration
- tránh để agent “auto-approve” che mất lỗi cấu hình nghiêm trọng mà không cảnh báo đủ mạnh

## Yêu cầu maintain consistency across the project

### Prompt consistency

- cùng một nguyên tắc cho mọi agent prompt
- không có agent nào dùng pattern riêng khó hiểu
- prompt seed, prompt API, prompt runtime phải cùng semantics

### Error handling consistency

- lỗi prompt config phải được phân loại thống nhất
- không nơi nào trả raw low-level exception khó hiểu cho user
- không nơi nào silently swallow lỗi cấu hình mà không có observability

### Testing consistency

- mọi agent có dynamic prompt phải có test cho prompt rendering safety
- mọi class bug kiểu brace escaping phải có regression tests
- seed prompts và runtime prompts phải được kiểm tra bằng cùng rule

### Documentation consistency

- docs prompt authoring phải nêu rõ:
  - cái gì được đưa vào `system_prompt`
  - cái gì bắt buộc phải đưa vào `user/input`
  - cách escape braces nếu thực sự cần literal braces

## Acceptance criteria

Chỉ được coi là hoàn thành khi thỏa toàn bộ điều kiện sau:

- `QA_CRITIC` không còn fail vì `ChatPromptTemplate` missing variables do JSON braces
- không còn prompt active nào inject raw rich payload vào `system_prompt` theo pattern nguy hiểm
- có rule/validation nhất quán cho prompt authoring trên toàn project
- có regression tests cho lớp lỗi brace escaping / double templating
- runtime error handling và observability cho prompt misconfiguration được chuẩn hóa

## Gợi ý triển khai

### Phase 1: Immediate fix

- sửa `QA_CRITIC` prompt
- rà `STORY_ARCHITECT`, `SCENE_SPLITTER`, và các prompt còn lại có system placeholders
- thêm regression tests

### Phase 2: Project-wide hardening

- thêm validation trong prompt manager / prompt admin API
- chuẩn hóa rule prompt architecture
- cập nhật docs prompt authoring

### Phase 3: Runtime safety

- thêm signal/health/metric cho prompt misconfiguration
- giảm silent fallback cho các agent quan trọng

## Lý do cần làm ngay

Lỗi này nguy hiểm vì:

- xuất hiện trước khi model thực thi
- retry không giúp gì
- có thể làm hệ thống “trông như chạy được” nhưng thực chất bỏ qua bước QA thật
- rất dễ lặp lại ở prompt khác nếu team tiếp tục author prompt mà không có rule nhất quán

Đây là bug thuộc lớp “design inconsistency”, không phải chỉ là typo trong một prompt.
