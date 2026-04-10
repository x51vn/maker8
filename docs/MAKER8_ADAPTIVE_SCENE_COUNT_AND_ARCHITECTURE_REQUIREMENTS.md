# Maker8 Requirements: Adaptive Scene Count And Updated Architecture Documentation

## 1. Purpose

Tài liệu này mô tả requirements cho hai vấn đề đang tồn tại:

1. số lượng `scenes` đang bị cố định theo một giả định cứng, trong khi business cần số scene thích ứng theo thời lượng video
2. hệ thống chưa có bộ tài liệu kiến trúc cập nhật, đủ ngắn gọn nhưng vẫn chính xác, có sơ đồ khối và sơ đồ luồng dữ liệu

Tài liệu này là requirements doc, không phải implementation plan chi tiết.

## 2. Current State

### 2.1 Scene count

Trong repo `maker8` hiện tại, renderer không tự sinh scene. `maker8` chỉ consume `render_spec.scenes[]` từ upstream và render đúng theo danh sách scene nhận được.

Điều này có nghĩa:

- vấn đề "fixed 5 scenes" không nằm ở renderer core của `maker8`
- vấn đề nằm ở upstream request generation / planning layer của hệ `editor8 -> maker8`
- requirement này là requirement cross-system, nhưng ownership chính của logic sinh số scene phải nằm ở upstream planner

### 2.2 Architecture documentation

Repo hiện đã có một số tài liệu kiến trúc và review như:

- `docs/MAKER8_SOURCE_OF_TRUTH.md`
- `docs/MAKER8_SYSTEM_ARCHITECTURE_AND_REVIEW.md`

Nhưng vẫn còn thiếu một tài liệu kiến trúc cập nhật theo nghĩa vận hành:

- mô tả current-state ngắn gọn, dễ đọc
- chỉ rõ boundary giữa `editor8`, `maker8`, Kafka, Dropbox, external connectors
- có sơ đồ khối
- có sơ đồ luồng dữ liệu
- đủ chính xác để dùng cho onboarding, review, vận hành, và thay đổi hệ thống

## 3. Scope And Ownership

### 3.1 In scope

- định nghĩa requirement để số lượng scene được quyết định theo thời lượng video thay vì số cố định
- định nghĩa requirement cho bộ tài liệu kiến trúc cập nhật
- làm rõ ownership giữa upstream planner và downstream renderer

### 3.2 Out of scope

- chưa chốt công thức duration-to-scene cuối cùng ở mức implementation
- chưa chọn UI/UX cụ thể cho nơi cấu hình scene policy
- chưa triển khai code thay đổi logic planner hoặc renderer

### 3.3 Ownership

| Area | Primary owner | Notes |
|---|---|---|
| Tính toán số scene theo thời lượng | Upstream planning layer (`editor8` hoặc service sinh `RenderRequest`) | `maker8` không nên tự ý tái chia scene nếu đã nhận `render_spec.scenes[]` |
| Render theo danh sách scene đã nhận | `maker8` | Phải hỗ trợ số scene biến thiên, không giả định cứng |
| Tài liệu kiến trúc hệ thống `editor8 -> maker8` | Architecture / Tech Lead | Có thể nằm ở một repo, nhưng phải phản ánh đúng current runtime |
| Tài liệu runtime chi tiết của `maker8` | `maker8` owners | Phải khớp với code, topics, stage flow, dependencies |

## 4. Requirement Group A: Adaptive Scene Count

## 4.1 Problem Statement

Số lượng scene cố định làm chất lượng output khó chấp nhận khi duration thay đổi mạnh:

- video ngắn nhưng vẫn bị chia quá nhiều scene
- video dài nhưng chỉ có ít scene, làm mỗi scene quá dài hoặc quá nặng thông tin
- pacing của narration, visual change, và editing rhythm không còn tỷ lệ với độ dài video

System cần chuyển từ `fixed scene count` sang `duration-aware scene planning`.

## 4.2 Functional Requirements

### A-1. Scene count must not be hard-coded

Hệ thống không được cố định số lượng scene theo một hằng số như `5`.

Số lượng scene phải được tính từ thời lượng đầu ra mục tiêu hoặc thời lượng nội dung đầu vào đã được planner xác định.

### A-2. Scene count must be derived from duration

Planner phải dùng duration làm input bắt buộc khi quyết định `scene_count`.

Nguồn duration có thể là một trong các loại sau:

- target duration của video
- estimated narration duration
- source media duration đã được chọn
- duration budget sau khi planning script/narration hoàn tất

Planner phải chỉ ra rõ mình dùng loại duration nào làm input chính.

### A-3. Scene count policy must be configurable

Logic tính `scene_count` không được bị hard-code sâu trong prompt hoặc code mà không có policy rõ ràng.

Policy tối thiểu phải cho phép cấu hình:

- target seconds per scene
- scene count tối thiểu
- scene count tối đa
- rule làm tròn
- optional override cho các format đặc biệt

### A-4. The planner must emit the final scene count explicitly

Sau khi planning xong, upstream phải phát ra `render_spec.scenes[]` với số phần tử đúng bằng `scene_count` đã chọn.

`maker8` sẽ coi đó là output contract đã chốt và không tự tái chia scene.

### A-5. The chosen scene count must be explainable

Hệ thống phải có khả năng giải thích vì sao chọn số scene đó.

Tối thiểu cần log hoặc metadata nội bộ các giá trị:

- duration input
- policy version
- target seconds per scene
- scene_count_before_bounds
- final_scene_count
- min/max bounds applied hay không

### A-6. Scene count must remain bounded

Adaptive không có nghĩa là vô hạn.

Planner phải luôn áp dụng guardrails để:

- tránh sinh quá ít scene cho video dài
- tránh sinh quá nhiều scene cho video ngắn
- tránh scene count cao hơn năng lực thực tế của content planner và renderer

### A-7. Renderer must support variable scene counts

`maker8` phải tiếp tục hoạt động đúng với số scene biến thiên, miễn là `render_spec` hợp lệ.

Không được thêm assumption mới kiểu:

- chỉ tối ưu cho 5 scene
- chỉ test cho 5 scene
- chỉ log/metric cho một ngưỡng scene count cứng

### A-8. Backward compatibility must be explicit

Trong giai đoạn chuyển đổi, hệ thống phải quyết định rõ:

- request cũ đang có 5 scene cố định có còn được chấp nhận không
- nếu còn, đó là backward compatibility hay default policy cũ
- từ mốc nào planner mới phải dùng adaptive scene count

Không được để trạng thái nửa cũ nửa mới mà không có versioning hoặc release note rõ ràng.

## 4.3 Non-Functional Requirements

### A-9. Deterministic planning

Với cùng input content, cùng duration input, cùng policy version, planner phải cho ra cùng `scene_count`, trừ khi hệ thống cố ý cho phép non-deterministic planning và đã log rõ.

### A-10. Operational observability

Operator phải nhìn được trong log hoặc artifact planning:

- duration target
- final scene count
- actual rendered scene count
- skipped scene count nếu có degrade ở downstream

### A-11. Testability

Hệ thống phải có test cho các bucket duration khác nhau, tối thiểu gồm:

- short-form
- medium-form
- long-form
- boundary cases tại điểm đổi scene count

## 4.4 Acceptance Criteria

Requirement Group A được xem là đạt khi:

1. không còn bất kỳ assumption chính thức nào rằng mọi video đều có 5 scene
2. có policy rõ ràng để suy ra `scene_count` từ duration
3. output planner thể hiện đúng số scene được chọn
4. `maker8` render được request có số scene khác nhau mà không cần special-case cho số 5
5. logs hoặc metadata cho phép audit lại quyết định chọn `scene_count`
6. có test hoặc fixture cover ít nhất nhiều hơn một mức duration

## 4.5 Recommended Design Direction

Đây là khuyến nghị, chưa phải contract cứng:

- giữ logic scene planning ở upstream
- coi `render_spec.scenes[]` là contract cuối cùng gửi sang renderer
- policy duration-to-scene nên cấu hình được thay vì viết chết trong prompt
- nếu cần, thêm planning metadata nội bộ để audit, nhưng không bắt buộc phải đưa toàn bộ metadata đó vào render contract public ngay lập tức

## 5. Requirement Group B: Updated Architecture Documentation

## 5.1 Problem Statement

Hệ thống hiện thiếu một bộ tài liệu kiến trúc cập nhật, ngắn gọn, và dùng được trong thực tế.

Thiếu hụt hiện tại không phải là "không có tài liệu", mà là:

- chưa có narrative rõ ràng và current-state oriented
- boundary giữa các thành phần chưa được mô tả theo cách dễ dùng cho vận hành
- chưa có sơ đồ khối và sơ đồ luồng dữ liệu như một phần bắt buộc của tài liệu
- nguy cơ doc drift vẫn cao khi code thay đổi

## 5.2 Functional Requirements

### B-1. Must have one canonical architecture document

Phải có một tài liệu kiến trúc canonical cho current-state system.

Tài liệu này phải đóng vai trò entry point chính cho người đọc muốn hiểu hệ thống.

### B-2. The architecture document must describe the full system boundary

Tài liệu phải mô tả tối thiểu các thành phần:

- `editor8` hoặc upstream request producer
- Kafka input topic
- `maker8` worker
- pipeline stages trong `maker8`
- local working storage
- TTS providers
- asset sources như HTTP / YouTube
- Dropbox hoặc output storage tương đương
- Kafka result / DLQ outputs

### B-3. Must include a block diagram

Tài liệu phải có sơ đồ khối mô tả:

- các thành phần runtime chính
- hướng kết nối giữa các thành phần
- boundary nội bộ và external dependency

Sơ đồ khối phải đủ rõ để người mới nhìn vào hiểu hệ thống trong vài phút.

### B-4. Must include a data flow diagram

Tài liệu phải có sơ đồ luồng dữ liệu mô tả ít nhất:

- input payload đi vào từ đâu
- dữ liệu nào được materialize ra disk
- asset flow qua resolve/download/normalize
- TTS flow theo scene
- rendered output đi tới đâu
- result/DLQ events được emit như thế nào

### B-5. Must separate control flow from data flow

Tài liệu không được trộn lẫn:

- stage execution order
- ownership / orchestration
- artifact movement / data movement

Ít nhất phải phân biệt rõ:

- control flow
- data flow
- external integrations

### B-6. Must reflect current runtime, not aspirational design

Tài liệu phải mô tả hệ thống đang chạy thực tế, không phải kiến trúc mong muốn trong tương lai.

Nếu có future design, phải tách riêng thành section `Future` hoặc `Planned`.

### B-7. Must define source-of-truth hierarchy

Tài liệu phải nói rõ:

- file nào là source of truth cho contract
- file nào là source of truth cho runtime architecture
- file nào là review / analysis / historical note

Nếu có nhiều tài liệu, phải có hierarchy rõ ràng để tránh đọc nhầm.

### B-8. Must define ownership and update rules

Tài liệu phải chỉ ra:

- ai chịu trách nhiệm cập nhật khi thay đổi kiến trúc
- khi nào tài liệu bắt buộc phải update
- PR nào cần update docs cùng code

### B-9. Must be diagram-renderable in repository tooling

Sơ đồ nên dùng format dễ review trong repo, ưu tiên:

- Mermaid
- hoặc ASCII diagram nếu Mermaid không phù hợp

Không nên phụ thuộc vào file ảnh rời khó maintain nếu không có lý do đặc biệt.

### B-10. Must cover operationally relevant details

Tài liệu kiến trúc phải bao gồm tối thiểu:

- topics
- stage order
- work directory / artifact layout
- retry / DLQ behavior
- health / status files hoặc health endpoints
- logging / metrics chính
- external dependencies

## 5.3 Non-Functional Requirements

### B-11. Concise but complete

Tài liệu phải đủ ngắn để có thể đọc hết trong một lần, nhưng vẫn đủ chi tiết để operator, developer, và reviewer hiểu đúng hệ thống.

### B-12. Maintainable

Mỗi sơ đồ và mỗi section phải dễ update khi code đổi.

Không nên tạo các sơ đồ quá chi tiết đến mức gần như không thể giữ đồng bộ.

### B-13. Reviewable

Diagram và narrative phải diff-friendly trong Git.

### B-14. Traceable to code

Các khẳng định quan trọng trong tài liệu phải có thể truy ra code hoặc config tương ứng.

## 5.4 Acceptance Criteria

Requirement Group B được xem là đạt khi:

1. có một tài liệu kiến trúc canonical mới hoặc đã refactor rõ ràng từ tài liệu hiện có
2. tài liệu đó có ít nhất một sơ đồ khối
3. tài liệu đó có ít nhất một sơ đồ luồng dữ liệu
4. tài liệu phân biệt được system boundary, runtime control flow, và data flow
5. tài liệu chỉ ra source-of-truth hierarchy
6. tài liệu phản ánh đúng current runtime của `maker8`
7. README hoặc quick reference có link rõ tới tài liệu kiến trúc canonical

## 5.5 Suggested Document Structure

Khuyến nghị tài liệu kiến trúc canonical có cấu trúc như sau:

1. Purpose
2. System context and boundaries
3. High-level block diagram
4. Runtime control flow
5. Data flow diagram
6. Pipeline stage responsibilities
7. External dependencies
8. Artifact layout on disk
9. Failure and retry model
10. Observability
11. Source-of-truth hierarchy
12. Known limitations

## 6. Cross-System Constraints

Hai requirement group này liên quan trực tiếp với nhau.

Nếu scene count trở nên adaptive, tài liệu kiến trúc cũng phải phản ánh rõ:

- scene planning diễn ra ở đâu
- ai quyết định `scene_count`
- `maker8` consume gì và không consume gì
- metadata nào được giữ cho audit

Không được cập nhật logic adaptive scene count mà vẫn để tài liệu kiến trúc mô tả mơ hồ như thể renderer tự sinh scene.

## 7. Delivery Requirements

Để coi là hoàn tất ở mức tài liệu và design alignment, cần có tối thiểu:

1. một requirements doc cho adaptive scene count và architecture documentation
2. một tài liệu kiến trúc canonical đã được update hoặc tạo mới
3. diagram block và data flow nằm trực tiếp trong repo
4. link từ tài liệu điều hướng chính sang tài liệu canonical

## 8. Open Decisions

Các quyết định sau cần được chốt ở bước design/implementation, chưa được coi là đã quyết trong tài liệu này:

- duration input chuẩn để tính scene count là gì
- công thức hoặc bucket nào dùng để map duration sang scene count
- min/max scene count mặc định cho từng content type
- planning metadata có đi vào public contract hay chỉ là internal audit metadata
- tài liệu canonical mới sẽ thay thế hoàn toàn hay chỉ refactor tài liệu hiện có

## 9. Summary

Yêu cầu thực chất là:

- bỏ tư duy `5 scenes by default`
- chuyển sang `scene count derived from duration and policy`
- đồng thời xây lại tài liệu kiến trúc current-state sao cho có thể đọc, review, và vận hành được

`maker8` phải tiếp tục là renderer tiêu thụ `render_spec.scenes[]`, còn trách nhiệm quyết định số scene phải được đặt đúng ở upstream planning layer và được phản ánh trung thực trong tài liệu kiến trúc hệ thống.
