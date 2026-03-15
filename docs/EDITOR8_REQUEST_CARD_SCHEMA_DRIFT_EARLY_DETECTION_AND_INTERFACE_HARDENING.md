# Editor8 Request Card: Eliminate Schema Drift And Harden Agent Interfaces

## 1. Summary

`editor8` đang có vấn đề mang tính hệ thống:

- schema drift giữa prompt contract, agent output, Pydantic models, validators, postprocessors, orchestrator
- inconsistency giữa các agent/interface
- lỗi thường chỉ bị phát hiện muộn, sau khi output đã đi sâu vào pipeline
- các bước đầu không đủ chặt để chặn dữ liệu sai shape trước khi lan sang bước sau

Yêu cầu của card này là refactor `editor8` để:

- phát hiện lỗi **ngay ở boundary đầu tiên**
- chuẩn hóa interface giữa mọi agent và mọi step
- loại bỏ drift giữa prompt, validator, postprocessor, model và orchestrator
- đảm bảo lỗi schema được bắt sớm, classify rõ, repair hoặc retry đúng chỗ

Mục tiêu không phải vá riêng lỗi `visual_concept=list[str]`. Mục tiêu là **fix triệt để class vấn đề schema drift / interface inconsistency trên toàn pipeline**.

## 2. Trigger Incident

Incident gần đây:

- `STORY_ARCHITECT` trả `scenes[*].visual_concept` là `list[str]`
- `StoryBlueprint` yêu cầu `visual_concept: str`
- output parse JSON thành công nhưng fail ở `StoryBlueprint.model_validate(...)`
- orchestrator fail ở planning phase
- job bị mark `FAILED`, publish DLQ, consumer commit offset

Điều này cho thấy:

- prompt đã yêu cầu `visual_concept (string)` nhưng LLM vẫn drift
- agent quality retry không chặn được output sai shape
- model layer đang coercion không nhất quán giữa các field tương tự
- lỗi schema bị phát hiện quá muộn, sau khi agent run đã “thành công”

## 3. Problem Statement

`editor8` hiện đang có nhiều lớp interface nhưng chưa có một contract discipline xuyên suốt:

1. prompt mô tả output kỳ vọng
2. agent runner parse JSON
3. validator quyết định có retry hay không
4. postprocessor normalize dữ liệu
5. Pydantic model validate shape cuối cùng
6. orchestrator consume artifact

Hiện nay các lớp này chưa đồng bộ hoàn toàn.

Hệ quả:

- cùng một loại dữ liệu nhưng field này được coercion, field khác thì không
- một số agent có validator riêng, một số agent thì passthrough
- prompt yêu cầu một kiểu dữ liệu nhưng validator không enforce
- postprocessor có cho vài agent nhưng thiếu cho các planning agents quan trọng
- lỗi shape bị bắt ở orchestrator thay vì ở agent boundary

Đây là dấu hiệu của **interface architecture chưa hoàn chỉnh**.

## 4. Root Causes

### 4.1 No canonical output contract enforcement per agent

Mỗi agent có prompt và model, nhưng không phải agent nào cũng có:

- validator riêng
- postprocessor riêng
- typed artifact boundary riêng

Kết quả là contract tồn tại trên giấy nhiều hơn là trên runtime path.

### 4.2 Validation is inconsistent across agents

Một số agent như `SCENE_SPLITTER` có validator khá rõ.

Nhưng các planning agents như:

- `STORY_ARCHITECT`
- `INTENT_ANALYZER`
- `QA_CRITIC`

chưa được kiểm soát cùng một tiêu chuẩn.

Điều này tạo ra inconsistency giữa các agent interfaces.

### 4.3 Postprocessing coverage is incomplete

Postprocessor hiện chỉ cover một subset agent types.

Những bước tạo ra planning artifact quan trọng lại chưa được normalize đủ mạnh trước khi vào model boundary.

### 4.4 Model coercion rules are ad hoc

Một số field chấp nhận LLM quirks, một số field tương tự lại không.

Ví dụ kiểu “bullet list nhưng đáng lẽ là string” là pattern phổ biến của LLM, nhưng hiện không được xử lý thống nhất.

### 4.5 Orchestrator still acts as late schema gate

Orchestrator hiện vẫn là nơi nhiều lỗi shape bị nổ ra.

Đây là quá muộn. Orchestrator nên nhận artifact đã được chuẩn hóa và validated, không phải đóng vai parser cuối cùng cho output không ổn định.

## 5. Required End State

Hệ thống phải đạt trạng thái sau:

### 5.1 Every agent has a formal interface contract

Mỗi agent phải có đầy đủ:

- prompt contract
- typed output schema
- validator
- postprocessor
- error classification rules

Không chấp nhận agent “parse_json=True nhưng thực chất passthrough”.

### 5.2 Schema errors are caught at the earliest possible boundary

Nếu agent trả output sai shape:

- phải bị phát hiện ngay sau parse / validation
- phải retry hoặc normalize ở đó
- không để data sai đi sâu vào orchestrator

### 5.3 All stages have explicit boundary validation

Không chỉ agent layer.

Mọi boundary giữa các bước phải có validation rõ:

- input message -> input model
- agent raw output -> parsed JSON
- parsed JSON -> normalized artifact
- normalized artifact -> typed domain model
- domain model -> next pipeline step

### 5.4 Drift must become visible during development, not only in production

Schema drift phải bị phát hiện qua:

- tests
- CI
- prompt linting
- contract compatibility checks

Không chờ tới khi worker chạy production mới nổ.

## 6. Refactor Requirements

## 6.1 Introduce canonical typed artifact contracts for every agent

Mỗi agent phải declare artifact type chính thức.

Ví dụ:

- `INTENT_ANALYZER -> InputIntent`
- `STORY_ARCHITECT -> StoryBlueprint`
- `QA_CRITIC -> NarrativeReview`
- `SCENE_SPLITTER -> SceneStoryboard`

Không được có khoảng mơ hồ giữa “JSON parse được” và “artifact hợp lệ”.

Phải có một helper hoặc abstraction thống nhất:

- parse raw output
- validate structure
- normalize common quirks
- instantiate typed model
- return typed artifact hoặc typed failure

## 6.2 Add validator coverage for every structured-output agent

Mọi agent có `parse_json=True` hoặc sinh structured artifact phải có validator riêng.

Validator phải kiểm:

- root type
- required fields
- field types
- cardinality
- semantic constraints cơ bản
- common LLM failure modes

Không được fallback sang `validate_passthrough` cho các agent tạo artifact cấu trúc.

## 6.3 Add postprocessor coverage for every structured-output agent

Mọi structured-output agent phải có deterministic postprocessor riêng.

Postprocessor phải xử lý:

- markdown/code fence cleanup
- list-to-string normalization khi policy cho phép
- trimming / dedup / default filling
- enum normalization
- null/empty normalization

Postprocessor phải idempotent và có test riêng.

## 6.4 Standardize coercion rules across similar fields

Nếu hệ thống chấp nhận một class LLM quirk, phải xử lý nhất quán trên các field tương tự.

Ví dụ:

- `narration_outline` chấp nhận `list[str] -> str`
- thì phải quyết định rõ `visual_concept` có chấp nhận pattern đó hay không

Yêu cầu:

- xây policy coercion rõ ràng
- reuse helper chung thay vì viết lẻ tẻ cho từng field
- document policy trong code và tests

## 6.5 Move schema enforcement earlier than orchestrator consumption

Orchestrator không được là nơi đầu tiên phát hiện output shape sai.

Yêu cầu:

- `run_*` helper của từng agent phải trả typed artifact đã hợp lệ
- lỗi schema phải được convert thành typed retryable/non-retryable agent failure trước khi ra khỏi helper
- orchestrator chỉ nhận domain artifact hoặc typed failure đã classify

## 6.6 Add interface-specific error taxonomy

Schema/interface failures phải được phân loại rõ, không chỉ ném raw `ValidationError`.

Ví dụ:

- `AGENT_JSON_PARSE_ERROR`
- `AGENT_SCHEMA_MISMATCH`
- `AGENT_MISSING_REQUIRED_FIELD`
- `AGENT_INVALID_FIELD_TYPE`
- `AGENT_POSTPROCESS_FAILED`
- `AGENT_MODEL_VALIDATION_FAILED`

Điều này giúp:

- retry đúng logic
- metrics tốt hơn
- operator hiểu đúng root cause

## 6.7 Add repair policy at the right boundary

Không phải schema nào cũng nên fail ngay.

Yêu cầu:

- define field-level normalization policy
- define retry policy
- define fail-fast policy

Ví dụ:

- `list[str] -> string` có thể normalize với một số field text
- enum sai casing có thể normalize
- missing required section có thể retry
- wrong root structure có thể retry
- inconsistent type ở field critical có thể retry rồi fail-fast nếu vẫn sai

Repair phải xảy ra **trước** orchestrator step kế tiếp.

## 6.8 Add contract parity checks between prompts, validators, models, and postprocessors

Mỗi agent contract phải được giữ đồng bộ giữa:

- prompt seed
- model schema
- validator
- postprocessor
- tests

Yêu cầu:

- thêm contract parity tests
- nếu prompt yêu cầu field `string`, validator/model/test cũng phải reflect điều đó
- nếu postprocessor chấp nhận coercion thì prompt/docs/test cũng phải ghi rõ

## 6.9 Add pipeline-wide boundary validation

Không chỉ agent layer.

Mỗi major step phải có boundary assertions:

- message ingestion
- planning
- scripting
- storyboard
- media search
- assembly
- repair
- publish handoff

Mỗi boundary phải có:

- typed input
- typed output
- explicit validation
- explicit error handling

## 7. Best Practices That Must Be Applied

## 7.1 Treat LLM output as untrusted input

LLM output luôn phải được xem là dữ liệu không đáng tin cậy.

Không được vì parse JSON thành công mà coi là artifact hợp lệ.

## 7.2 One interface, one contract, one validator, one postprocessor

Mỗi structured interface phải có bộ contract đầy đủ, không partial.

## 7.3 Fail fast, but fail at the correct layer

Fail càng sớm càng tốt, nhưng phải fail đúng boundary.

Không đẩy lỗi shape xuống orchestrator hoặc persistence layer mới phát hiện.

## 7.4 Normalize intentionally, not accidentally

Coercion chỉ được phép khi:

- đã có policy rõ
- có test
- không làm mất meaning quan trọng

Không được normalize âm thầm tùy tiện.

## 7.5 Keep validators deterministic and cheap

Validator không được phụ thuộc vào LLM khác hoặc side effect.

Nó phải:

- deterministic
- fast
- reliable

## 7.6 Make error classes actionable

Operator và developer phải nhìn log/DLQ là biết:

- fail ở đâu
- raw output sai kiểu gì
- retry có hợp lý không
- fix nên ở prompt, validator, model hay postprocessor

## 7.7 Lock behavior with tests, not memory

Mọi incident schema drift sau khi fix phải được chuyển thành test.

Không chấp nhận kiểu “biết rồi, cẩn thận lần sau”.

## 8. Required Deliverables

## 8.1 Architecture deliverables

- tài liệu hóa agent interface lifecycle
- sơ đồ boundary validation cho toàn pipeline
- policy chung cho coercion / retry / fail-fast / repair

## 8.2 Code deliverables

- validator riêng cho mọi structured-output agent
- postprocessor riêng cho mọi structured-output agent
- typed artifact wrappers/helpers thống nhất
- standardized schema error taxonomy
- early boundary validation integration

## 8.3 Test deliverables

Phải có coverage cho:

- list-vs-string drift
- missing field drift
- wrong enum/value drift
- prompt/model mismatch
- validator catches issue before orchestrator consumes artifact
- postprocessor normalizes known LLM quirks deterministically
- agent retry is triggered for schema-invalid output

## 8.4 CI deliverables

CI phải fail nếu:

- structured agent thiếu validator
- structured agent thiếu postprocessor
- parity tests giữa prompt/schema/model fail
- regression fixtures không parse/normalize đúng

## 9. Definition Of Done

Chỉ coi là hoàn thành khi:

- không còn structured agent nào chạy bằng `parse_json=True` mà không có validator/postprocessor tương ứng
- schema drift được chặn ở bước đầu tiên của interface đó
- orchestrator không còn là nơi đầu tiên phát hiện lỗi shape của agent output
- logs/DLQ phản ánh đúng error taxonomy của schema/interface failures
- test suite có regression coverage cho các drift patterns phổ biến
- CI có gate để ngăn drift tái xuất hiện

## 10. Final Requirement

`editor8` phải được refactor để mọi interface trong pipeline đều có contract rõ ràng, validated sớm, normalized có chủ đích, và được test khóa lại.

Mục tiêu cuối cùng là:

> lỗi schema phải được phát hiện ngay từ bước đầu tiên và ở tất cả các bước, thay vì nổ muộn trong orchestrator hoặc production runtime.
