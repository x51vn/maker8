# Maker8 Go-Live Investigation Guide

## 1. Mục tiêu

Hướng dẫn này dùng cho `Architecture`, `PO` và `Developer` để cùng investigate hệ thống `maker8` với 5 mục tiêu:

1. Tìm ra các điểm không thống nhất giữa `docs`, `schema`, `example payload`, `code`, và runtime behavior.
2. Tìm ra các điểm `interface drift` ở boundary `editor8 -> maker8`, giữa các stage nội bộ, và ở boundary output `maker8 -> downstream`.
3. Chỉ ra các `gap` còn phải lấp đầy trước khi có thể `go-live` an toàn.
4. Bắt buộc cover đầy đủ các khía cạnh `production readiness`, không chỉ ở mức code chạy được mà còn ở mức vận hành được, kiểm soát rủi ro được, và bàn giao được.
5. Kết thúc bằng một session compact tài liệu thành một file thống nhất, đóng vai trò `source of truth`.

### 1.1 Ngữ cảnh sản phẩm

Mục đích của hệ thống này là `sản xuất video tin tức`.

Vì vậy, review `go-live` không được giới hạn ở render pipeline. Nhóm bắt buộc phải cover đủ cả:

- tính đúng của nội dung đầu ra
- tính ổn định của pipeline sản xuất
- tốc độ xử lý đủ cho nhịp xuất bản tin tức
- khả năng trace nguồn, attribution và audit
- khả năng xử lý incident trong production
- rủi ro copyright, policy, và compliance liên quan đến nội dung tin tức

## 2. Phạm vi investigate

Phạm vi của buổi investigate này gồm:

- Input contract `video.render.request.v1`
- Output contract `video.render.result.v1` và `video.render.dlq.v1`
- Runtime pipeline 8 stage của `maker8`
- Runtime flags, config, retry, degraded mode, observability, cleanup
- Tính nhất quán giữa code và tài liệu
- Tính rõ ràng của ownership giữa `PO`, `Architecture`, `Developer`
- Editorial flow phục vụ sản xuất video tin tức
- Source attribution, provenance, usage rights, policy/compliance của asset và metadata
- Production operations: deploy, rollback, alerting, runbook, incident response
- Reliability, scalability, capacity, latency, cost, secret management, backup/recovery
- Handover readiness để team có thể vận hành hệ thống sau go-live

Không nên biến session này thành buổi sửa code ngay. Mục tiêu là xác minh, phân loại, chốt gap, và ra quyết định rõ ràng.

## 3. Nguồn bằng chứng bắt buộc

Mọi kết luận phải dựa trên bằng chứng. Thứ tự ưu tiên khi có mâu thuẫn:

1. Runtime code đang chạy
2. Canonical contract / typed model
3. JSON schema và example payload
4. Tài liệu kiến trúc / specs
5. Status report / request card / ghi chú cũ

Trong repo này, nhóm phải đối chiếu tối thiểu các nguồn sau:

### 3.1 Code và contract

- `src/render_contracts/render_spec.py`
- `src/render_contracts/events.py`
- `src/maker8/pipeline/orchestrator.py`
- `src/maker8/pipeline/context.py`
- `src/maker8/pipeline/validate.py`
- `src/maker8/pipeline/emit.py`
- `src/maker8/models/contracts.py`
- `src/maker8/models/spec.py`

### 3.2 Tài liệu hệ thống

- `README.md`
- `docs/maker8-specs.md`
- `docs/MAKER8_SYSTEM_ARCHITECTURE_AND_REVIEW.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `CONTRACT_FIELD_STATUS.md`

### 3.3 Schemas và fixtures

- `docs/schemas/render_request.schema.json`
- `docs/schemas/render_result.schema.json`
- `docs/schemas/dlq_payload.schema.json`
- `docs/examples/render_request.example.json`
- `docs/examples/render_request_minimal.example.json`
- `docs/examples/render_result_success.example.json`
- `docs/examples/render_result_failed.example.json`
- `docs/examples/dlq_payload.example.json`

### 3.4 Request cards liên quan đến drift/gap

Chỉ dùng làm nguồn tham khảo, không dùng làm source of truth:

- `docs/EDITOR8_MAKER8_REQUEST_CARD_CONTRACT_CONSISTENCY.md`
- `docs/EDITOR8_REQUEST_CARD_SCHEMA_DRIFT_EARLY_DETECTION_AND_INTERFACE_HARDENING.md`
- các request card khác có liên quan tới contract, uploader, degraded mode, observability

## 4. Nguyên tắc làm việc

### 4.1 Mỗi claim phải được gắn nhãn

Mỗi phát hiện phải được gắn đúng loại:

- `Doc drift`: docs nói khác code
- `Interface drift`: producer/consumer, stage/stage, hoặc request/result không khớp
- `Behavior gap`: schema có field nhưng runtime chưa thực thi đúng semantics
- `Operational gap`: thiếu logging, metrics, runbook, retry rule, alerting, secret/process
- `Test gap`: chưa có test chứng minh behavior hoặc compatibility

### 4.2 Phân biệt rõ 5 trạng thái field

Mỗi field phải được phân loại thành đúng một trạng thái:

- `Supported end-to-end`
- `Pass-through`
- `Reserved`
- `Deprecated`
- `Unknown / inconsistent`

Không chấp nhận trạng thái mơ hồ kiểu "có vẻ support".

### 4.3 Tách biệt 3 loại kết luận

- `Fact`: đã được chứng minh bằng code/schema/runtime
- `Inference`: suy ra từ nhiều bằng chứng nhưng chưa được test runtime
- `Open question`: chưa có đủ bằng chứng, cần follow-up

### 4.4 Mọi gap phải có owner và deadline

Nếu một gap đủ nghiêm trọng để ảnh hưởng go-live thì bắt buộc phải có:

- owner
- action
- mức độ nghiêm trọng
- quyết định `fix before go-live` hay `accept with risk`

### 4.5 Không được kết luận ready nếu chưa cover đủ production dimensions

Review chỉ được xem là đủ nếu đã cover tối thiểu các dimension sau:

- Product/use-case fit cho sản xuất video tin tức
- Contract và interface stability
- Runtime behavior và failure handling
- Content quality và editorial safeguards
- Source attribution, copyright, policy, compliance
- Observability, incident response, runbook
- Security và secret management
- Capacity, performance, cost
- Deployment, release, rollback, environment readiness
- Testing, UAT, sign-off và ownership sau go-live

## 5. Vai trò và trách nhiệm

### 5.1 PO

PO chịu trách nhiệm xác minh:

- field nào là business-critical
- behavior nào bắt buộc phải đúng trước go-live
- field nào chỉ là future/reserved
- output/result nào downstream thực sự cần
- trường hợp `degraded`, `partial`, `failed`, `dry_run` có chấp nhận được không
- acceptance criteria cho go-live
- tiêu chí chất lượng đầu ra của video tin tức
- SLA/SLO về thời gian từ lúc nhận job tới lúc có video sẵn sàng dùng
- yêu cầu editorial như tiêu đề, mô tả, attribution, thumbnail, metadata, ngôn ngữ, brand safety

### 5.2 Architecture

Architecture chịu trách nhiệm xác minh:

- source of truth nằm ở đâu
- boundary ownership giữa `editor8`, `maker8`, downstream
- versioning policy của contract
- field/status matrix có nhất quán không
- semantics của retry, DLQ, degraded mode, observability, failure taxonomy
- tài liệu nào giữ lại, tài liệu nào archive
- production topology, release path, rollback path, secret boundary, dependency boundary
- non-functional requirements: reliability, capacity, latency, recovery, security

### 5.3 Developer

Developer chịu trách nhiệm xác minh:

- code nào thực sự consume field nào
- stage nào đọc/ghi artifact nào
- behavior runtime hiện tại khác docs ở đâu
- test nào đang thiếu
- metrics/logging nào chưa đủ để vận hành
- change nào nhỏ có thể fix ngay, change nào cần card riêng
- dependency nào có thể fail trong production và hệ thống hiện chịu được tới đâu
- vùng nào còn thiếu health signal, retry guard, cleanup guard, validation guard

## 6. Deliverable của buổi investigate

Kết thúc buổi review phải có đủ 6 deliverable:

1. `Drift register`
2. `Gap register`
3. `Go-live blocker list`
4. `Decision log`
5. `Production readiness matrix`
6. `Single-source document outline`

Nhóm nên dùng một bảng chung với format tối thiểu sau:

| ID | Type | Surface | Evidence A | Evidence B | Impact | Severity | Owner | Decision | Due date |
|----|------|---------|------------|------------|--------|----------|-------|----------|----------|
| D-001 | Doc drift | RenderRequest | `src/render_contracts/render_spec.py` | `CONTRACT_FIELD_STATUS.md` | PO/dev hiểu sai field | High | Architecture | Update matrix | 2026-04-xx |

Severity đề nghị:

- `Blocker`: phải fix trước go-live
- `High`: có thể go-live chỉ khi có explicit risk acceptance
- `Medium`: chấp nhận tạm thời nếu có mitigation
- `Low`: cleanup/documentation backlog

`Production readiness matrix` nên có ít nhất các cột sau:

| Dimension | Current state | Evidence | Risk | Owner | Decision | Target date |
|-----------|---------------|----------|------|-------|----------|-------------|
| Editorial quality | Unknown | UAT chưa có | Xuất video sai format/ngữ cảnh tin tức | PO | Define UAT | 2026-04-xx |

## 7. Cách chạy buổi investigate

Khuyến nghị chạy thành `4 session review chính` và `1 session compact tài liệu` ở cuối.

## 7.1 Session 0: Chuẩn bị bằng chứng

Thời lượng gợi ý: `45-60 phút`

Mục tiêu:

- chốt baseline commit hoặc branch đang review
- chốt danh sách tài liệu được review
- tạo `drift register`
- thống nhất rule: code thắng docs khi có conflict, trừ khi business owner xác nhận behavior hiện tại là bug

Checklist:

- chụp snapshot danh sách tài liệu
- chốt scope review cho boundary `editor8 -> maker8`
- chốt người ghi biên bản
- chốt format severity
- chốt definition của `go-live blocker`

Output:

- 1 board/tài liệu tracking chung
- 1 danh sách nguồn bằng chứng
- 1 danh sách câu hỏi mở ban đầu

## 7.2 Session 1: Boundary và contract alignment

Thời lượng gợi ý: `90-120 phút`

Mục tiêu:

- review toàn bộ `RenderRequest`, `RenderResult`, `DLQPayload`
- xác định field nào active, pass-through, reserved, deprecated
- tìm doc drift và interface drift ở wire contract

### Checklist cho `RenderRequest`

Đối với từng field top-level và nested field, hỏi 7 câu:

1. Field này có tồn tại trong `src/render_contracts/render_spec.py` không?
2. Field này có trong schema JSON và example payload không?
3. Field này có được parse ở `orchestrator` không?
4. Field này có được đưa vào `PipelineContext` hay stage nào đó không?
5. Field này có semantics rõ ràng hay chỉ được pass-through?
6. Docs hiện tại mô tả đúng behavior đó không?
7. Có test hoặc fixture chứng minh behavior không?

### Checklist cho `RenderResult` và `DLQPayload`

Đối với từng field output:

1. Field này có đúng với downstream expectation không?
2. Field này có luôn xuất hiện hay chỉ trong một số case?
3. Với `FAILED`, `PARTIAL`, `DONE`, shape có ổn định không?
4. Docs, schema, examples có khớp nhau không?
5. Có field nào downstream sẽ dùng nhưng chưa được maker8 emit ổn định không?

### Những điểm nên review đầu tiên trong repo hiện tại

Đây là các điểm có xác suất drift cao và nên kiểm tra sớm:

- `RenderRequest.dry_run`
- `RenderRequest.canvas_profile`
- `RenderRequest.publish_intent`
- `RenderRequest.uploader_metadata`
- `RenderRequest.result.topic`
- `RenderRequest.result.key`
- `Canvas.safe_area`
- `SceneTiming.duration_mode`
- `Layer.align`
- `Transition.type`
- `PublishTarget.metadata`
- `PublishTarget.params`

### Dấu hiệu drift đã nên kiểm chứng ngay

Ngay trong repo hiện tại đã có vài điểm đáng để xác minh:

- `src/render_contracts/render_spec.py` có thêm `dry_run`, `canvas_profile`, `publish_intent`, `uploader_metadata`, nhưng không phải mọi tài liệu field-level đều phản ánh đủ.
- `src/maker8/pipeline/emit.py` và `src/maker8/pipeline/orchestrator.py` đã dùng `result.topic` và `result.key`, nên các tài liệu cũ nói hai field này hoàn toàn bị ignore cần được kiểm tra lại.

## 7.3 Session 2: Runtime walkthrough theo stage

Thời lượng gợi ý: `120 phút`

Mục tiêu:

- đi qua 8 stage theo thứ tự runtime
- xác định rõ input/output/precondition/failure mode/retryability
- tìm interface drift giữa stage docs và stage behavior

Stage cần review:

1. `VALIDATE`
2. `RESOLVE_ASSETS`
3. `DOWNLOAD`
4. `NORMALIZE`
5. `TTS`
6. `RENDER`
7. `UPLOAD_DROPBOX`
8. `EMIT_RESULT`

Cho mỗi stage, điền mẫu sau:

| Stage | Input contract | Output artifact | Reads from context | Writes to context | Retryable | Failure codes | Log/Metrics | Doc source | Drift/Gap |
|-------|----------------|-----------------|--------------------|-------------------|-----------|---------------|-------------|------------|-----------|

Những câu hỏi bắt buộc:

- Stage có validate boundary đầu vào đủ sớm chưa?
- Stage có đọc implicit state từ filesystem mà docs chưa mô tả không?
- Retry có đúng loại lỗi không?
- Output của stage có được stage sau consume đúng như docs nói không?
- Stage có degrade gracefully không, hay fail cứng?
- Có metric/log nào cần để production nhưng hiện chưa đủ?

## 7.4 Session 3: Go-live readiness review

Thời lượng gợi ý: `90 phút`

Mục tiêu:

- chốt các gap thực sự ảnh hưởng production readiness
- phân nhóm thành `must-fix`, `mitigation`, `post-go-live`

Review theo 10 nhóm:

### A. Contract readiness

- Có một source of truth duy nhất cho wire contract chưa?
- Schema, examples, code, docs có đồng bộ chưa?
- Downstream có hiểu đúng `DONE`, `PARTIAL`, `FAILED`, `DLQ` không?
- Có field nào đang "accept nhưng không honor" không?

### B. Runtime semantics

- `1 instance = 1 job` có được ghi rõ và chấp nhận về mặt vận hành chưa?
- Retry policy có đủ rõ cho từng stage chưa?
- `degraded` và `partial success` có semantics rõ không?
- `dry_run` có behavior tài liệu hóa đầy đủ chưa?

### C. External dependency readiness

- Kafka config và topic ownership đã rõ chưa?
- Dropbox upload contract đã ổn định chưa?
- TTS provider failover/rotation đã có docs và observability chưa?
- Connector `youtube` / `http` có boundary và failure mode rõ chưa?

### D. Operability

- Có log theo `job_id`, `job_key`, `correlation_id` xuyên suốt chưa?
- Có metrics cho invalid payload, retry, failed stage, job duration chưa?
- Có runbook cho DLQ, retry, stuck job, credential issue, storage cleanup chưa?
- Shutdown behavior và cleanup semantics có được chấp nhận chưa?

### E. Test coverage

- Có fixture parse test cho các payload chuẩn chưa?
- Có regression test cho các drift đã biết chưa?
- Có test chứng minh các field "supported" thực sự được honor không?
- Có consumer-driven contract test với payload gần thực tế production không?

### F. Documentation readiness

- Có tài liệu nào đang mâu thuẫn nhau không?
- Có tài liệu nào chỉ còn giá trị lịch sử, không nên dùng làm reference chính không?
- Có owner duy trì source-of-truth doc sau go-live không?

### G. Product và editorial readiness

- Video đầu ra có đạt format tin tức mà business cần không?
- Có checklist review về title, summary, source attribution, thumbnail, CTA, category, visibility không?
- Có chặn được trường hợp narration, asset, metadata, visual không khớp cùng một bản tin không?
- Có quy tắc rõ cho `degraded mode`: khi nào vẫn được phát hành, khi nào phải chặn?

### H. Content safety, copyright, policy, compliance

- Asset đầu vào có trace được nguồn không?
- Có đủ metadata để attribution và audit không?
- Có nguy cơ dùng asset không có quyền sử dụng không?
- Có quy tắc xử lý nội dung nhạy cảm, sai sự thật, hoặc vi phạm policy platform không?
- Có owner phê duyệt risk acceptance cho các vùng chưa tự động kiểm soát được không?

### I. Security và environment readiness

- Secrets có nằm đúng nơi và có rotation plan không?
- Môi trường `dev`, `staging`, `production` có tách biệt đủ không?
- Kafka, Dropbox, TTS credentials có quyền tối thiểu cần thiết không?
- Có audit trail cho thay đổi config production không?

### J. Capacity, performance, cost, release readiness

- Throughput hiện tại có đủ cho số lượng video tin tức dự kiến mỗi ngày/giờ không?
- Latency từ request tới result có đáp ứng nhu cầu xuất bản không?
- CPU, memory, GPU, network, Dropbox, TTS cost đã được ước lượng chưa?
- Có release checklist, rollback checklist, smoke test sau deploy và người trực incident không?

## 8. Cách nhận diện inconsistency, drift và gap

### 8.1 Khi nào là inconsistency

Gắn nhãn `inconsistency` khi ít nhất hai nguồn cùng mô tả một surface nhưng khác nhau về:

- field tồn tại hay không
- default value
- required/optional
- trạng thái `supported/pass-through/reserved`
- retryable hay non-retryable
- topic/key routing
- semantics của status `DONE/PARTIAL/FAILED`

### 8.2 Khi nào là interface drift

Gắn nhãn `interface drift` khi input/output của một boundary đã thay đổi hoặc được hiểu khác nhau:

- `editor8` emit field mà `maker8` không dùng hoặc hiểu khác
- `maker8` emit field mà downstream chưa expect
- schema/examples/docs còn shape cũ
- stage trước ghi artifact theo shape A, stage sau đọc theo shape B

### 8.3 Khi nào là go-live gap

Gắn nhãn `go-live gap` khi thiếu một trong các điều kiện sau:

- semantics rõ
- owner rõ
- test chứng minh behavior
- observability đủ để vận hành
- docs đủ để handover
- risk acceptance được chấp thuận
- editorial/process control đủ để xuất bản video tin tức an toàn
- production operation đủ để xử lý sự cố thật

## 9. Mẫu phân loại gap

Nhóm nên gom gap vào 1 trong các nhóm này:

- `Contract gap`
- `Behavior gap`
- `Schema/example gap`
- `Observability gap`
- `Deployment/ops gap`
- `Security/secret gap`
- `Test gap`
- `Documentation gap`

Mỗi gap phải ghi rõ:

- hiện trạng
- impact
- evidence
- decision
- owner
- target date

## 10. Definition of Done cho investigation

Buổi investigate chỉ được xem là hoàn tất khi tất cả điều kiện sau đúng:

1. Mọi field quan trọng ở request/result đã được phân loại trạng thái.
2. Mọi conflict giữa docs và code đã được ghi vào `drift register`.
3. Mọi `go-live blocker` đã có owner và quyết định.
4. Đã có `production readiness matrix` cover đủ technical, product, operations, security, compliance, editorial.
5. Đã có danh sách tài liệu giữ lại, tài liệu archive, tài liệu merge.
6. Đã chốt outline cho file source-of-truth thống nhất.

## 11. Session compact tài liệu thành 1 file thống nhất

Đây là session bắt buộc sau khi đã có drift/gap register.

Thời lượng gợi ý: `120 phút`

Mục tiêu:

- compact nhiều tài liệu rời rạc thành 1 file thống nhất
- loại bỏ duplicate narrative
- giữ schemas/examples riêng như artifact tham chiếu, nhưng chỉ có 1 narrative doc chính
- làm rõ toàn bộ điều kiện để hệ thống có thể go-live production cho use case sản xuất video tin tức

## 11.1 Thành phần tham gia

- `PO`: chốt business intent, supported scope, go-live criteria
- `Architecture`: chốt source of truth, ownership, versioning, information architecture
- `Developer`: chốt behavior runtime, field usage, operational notes, references vào code

## 11.2 Input của session compact

Tối thiểu mang vào phòng các tài liệu sau:

- `README.md`
- `docs/maker8-specs.md`
- `docs/MAKER8_SYSTEM_ARCHITECTURE_AND_REVIEW.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `CONTRACT_FIELD_STATUS.md`
- schemas và example payload trong `docs/schemas/` và `docs/examples/`
- drift register và gap register từ các session trước

## 11.3 Quy tắc compact

1. Chỉ giữ lại fact đã được xác minh.
2. Nếu hai tài liệu mâu thuẫn, giữ nội dung bám code/runtime mới nhất.
3. Không copy nguyên văn mọi thứ vào file mới; chỉ giữ nội dung còn giá trị vận hành.
4. Request card và audit note không nên là source-of-truth; chuyển thành appendix hoặc archive.
5. Mỗi field/behavior chỉ nên được mô tả một lần trong narrative chính.
6. Mọi unsupported/reserved field phải được nói rõ, không bỏ lửng.

## 11.4 Cách chạy session compact

### Bước 1: Phân loại tài liệu hiện có

Mỗi tài liệu được gắn đúng một nhãn:

- `Keep as reference`
- `Merge into source-of-truth`
- `Archive`
- `Split`

### Bước 2: Chốt file đích duy nhất

Khuyến nghị tạo một file narrative chính, ví dụ:

- `docs/MAKER8_SOURCE_OF_TRUTH.md`

Tên có thể đổi, nhưng chỉ nên có một file đóng vai trò authoritative narrative.

### Bước 3: Chốt skeleton của file thống nhất

Skeleton khuyến nghị:

1. Purpose và scope
2. Business use case: sản xuất video tin tức
3. System context và boundaries
4. Runtime architecture
5. Input/Output contracts
6. Field status matrix
7. Pipeline stage semantics
8. External dependencies và configuration
9. Failure handling, retry, degraded mode, DLQ
10. Production readiness requirements
11. Editorial, attribution, compliance, policy constraints
12. Operational readiness, observability, incident response
13. Known gaps / reserved fields / limitations
14. Example payloads, schemas, references
15. Decision log / changelog

### Bước 4: Chép nội dung theo nguyên tắc "merge, không accumulate"

Khi copy từ tài liệu cũ sang file mới:

- bỏ nội dung trùng
- bỏ trạng thái đã lỗi thời
- rewrite các section mơ hồ thành statement rõ ràng
- thêm link tới file code nơi cần chứng minh behavior

### Bước 5: Chốt owner cho việc duy trì file thống nhất

Phải có owner rõ sau session:

- ai update khi contract đổi
- ai update khi runtime semantics đổi
- ai duyệt khi field chuyển từ `reserved` sang `supported`

## 11.5 Output của session compact

Session compact chỉ hoàn tất khi có:

1. 1 file narrative thống nhất
2. 1 danh sách tài liệu bị archive/giảm vai trò
3. 1 decision log cho các conflict đã giải quyết
4. 1 owner list cho maintenance sau này
5. 1 section production-readiness rõ ràng cho mục tiêu sản xuất video tin tức

## 12. Gợi ý quyết định go-live

Sau toàn bộ review, nhóm nên chốt từng gap theo 1 trong 3 quyết định:

- `Fix before go-live`
- `Go-live with mitigation`
- `Accept risk and backlog`

Không nên dùng kết luận kiểu "để sau xem tiếp".

Với use case `video tin tức`, chỉ nên chốt `READY` khi cả 3 nhóm cùng ký nhận:

- `PO`: output đáp ứng nhu cầu xuất bản
- `Architecture`: production design đủ an toàn và vận hành được
- `Developer`: runtime behavior, test, observability, deploy path đủ tin cậy

## 13. Mẫu kết luận cuối buổi

Nhóm có thể kết thúc buổi bằng format ngắn sau:

```text
Go-live status: BLOCKED | CONDITIONAL | READY

Blockers:
1. ...
2. ...

High-risk accepted items:
1. ...

Docs to merge:
1. README.md
2. docs/maker8-specs.md
3. docs/MAKER8_SYSTEM_ARCHITECTURE_AND_REVIEW.md
4. docs/IMPLEMENTATION_STATUS.md
5. CONTRACT_FIELD_STATUS.md

Single source of truth target:
- docs/MAKER8_SOURCE_OF_TRUTH.md

Owners:
- PO: ...
- Architecture: ...
- Developer: ...
```

## 14. Kết luận

Mục tiêu của hướng dẫn này không phải tạo thêm một tài liệu mô tả hệ thống. Mục tiêu là ép ba vai trò `PO`, `Architecture`, `Developer` cùng nhìn vào một bộ bằng chứng, chỉ ra drift/gap một cách có hệ thống, rồi hợp nhất mọi mô tả quan trọng về hệ thống thành một `source of truth` duy nhất đủ tin cậy để go-live.
