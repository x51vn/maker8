# PRD: Editor8 UI-Managed Keys With Plain Text Database Storage

## 1. Purpose

Tài liệu này mô tả product requirements cho việc chuyển cách quản lý các keys hiện đang được `editor8` sử dụng từ file-based configuration sang quản lý trên giao diện UI của `editor8`, với dữ liệu được lưu **plain text trực tiếp trong database của `editor8`**.

Phase này **chưa dùng vault**. Yêu cầu của phase này là:

- keys phải được quản lý trên UI
- keys phải được lưu plain text trong database của `editor8`
- behavior phải nhất quán trên toàn hệ thống `editor8 -> maker8`

Đây là PRD. Đây không phải implementation plan chi tiết.

## 2. Project Context

### 2.1 System context

Theo current codebase và tài liệu kiến trúc hiện có, `maker8` là render worker trong hệ thống `editor8 -> maker8`.

Luồng chính hiện tại là:

```text
editor8
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
  -> Kafka: video.render.result.v1
  -> Kafka: video.render.dlq.v1
```

`maker8` không phải UI application. `maker8` là background worker tiêu thụ request, xử lý media, gọi TTS providers, upload Dropbox và phát kết quả về Kafka.

### 2.2 Current Codebase Evidence

Current codebase cho thấy runtime hiện tại đang phụ thuộc mạnh vào file/env-based secret management:

- Dropbox credentials được lấy từ env trong [`src/maker8/config.py`](../src/maker8/config.py).
- `.env.example` yêu cầu điền trực tiếp:
  - `MAKER8_DROPBOX_APP_KEY`
  - `MAKER8_DROPBOX_APP_SECRET`
  - `MAKER8_DROPBOX_REFRESH_TOKEN`
  - `MAKER8_ELEVENLABS_API_KEY`
  - `MAKER8_GOOGLE_APPLICATION_CREDENTIALS`
  trong [`.env.example`](../.env.example).
- TTS presets hiện nằm trong file [`config/tts_presets.json`](../config/tts_presets.json).
- Google Cloud multi-key rotation hiện đọc từ thư mục `gg-tts-keys/` qua [`src/maker8/services/key_ring.py`](../src/maker8/services/key_ring.py) và [`src/maker8/services/tts_client.py`](../src/maker8/services/tts_client.py).
- ElevenLabs multi-key rotation hiện đọc từ thư mục `elevenlabs-keys/` qua cùng các module trên.
- README cũng đang document file/env-based setup cho Dropbox, TTS presets và key directories trong [`README.md`](../README.md).

Một điểm boundary quan trọng:

- request contract `editor8 -> maker8` hiện **không mang secret fields**
- nghĩa là keys hiện không đi trong Kafka request
- do đó secret ownership hợp lý phải nằm ở `editor8` control plane / configuration layer, không nằm trong public render request contract

### 2.3 Repository Boundary

Repo hiện tại là repo `maker8`, không phải full monorepo chứa source code của `editor8`.

Vì vậy, PRD này dựa trên:

- current `maker8` codebase
- architecture docs mô tả boundary `editor8 -> maker8`
- current runtime requirements mà `editor8` phải phục vụ

Tài liệu này không giả định rằng đã review trực tiếp source code UI/backend của `editor8`. Ownership của `editor8` trong PRD được xác định từ system boundary và operational responsibility, không phải từ local source tree của repo này.

## 3. Problem Statement

Hiện tại keys đang được cấu hình bằng file hoặc env variables. Cách này tạo ra các vấn đề vận hành và sản phẩm:

1. không có UI chính thức để quản lý keys
2. thay đổi key đòi hỏi can thiệp file/deploy/runtime trực tiếp
3. khó kiểm soát ai đã tạo, sửa, tắt hoặc xoá key nào
4. khó giữ consistency giữa:
   - key inventory
   - preset configuration
   - provider enablement
   - runtime behavior
5. team non-dev hoặc operator không thể self-service qua UI

System cần chuyển từ:

- file-based secret/config management

sang:

- UI-managed key management ở `editor8`
- database-backed storage ở `editor8`

trong đó phase đầu chấp nhận lưu **plain text** trong database thay vì dùng vault.

## 4. Product Principles

### P-1. Editor8 là nơi quản lý key

`editor8` phải là nơi user/operator nhìn thấy, tạo, sửa, bật/tắt và gắn keys vào provider configuration.

### P-2. Plain text database storage là quyết định explicit của phase này

Phase này **không** triển khai vault, secret manager, KMS envelope encryption ở application layer, hoặc file-based keystore.

Yêu cầu của phase này là lưu raw secret value dưới dạng **plain text** trong database của `editor8`.

### P-3. Secret không được đưa vào render request contract

Kafka `RenderRequest` gửi sang `maker8` không được mở rộng để mang raw keys.

Boundary contract giữa `editor8` và `maker8` phải tiếp tục sạch khỏi secret material.

### P-4. UI management phải đi cùng consistency rules

Không chỉ thêm form nhập key. Hệ thống phải giữ consistency giữa:

- provider type
- key material
- status của key
- preset mapping
- runtime configuration materialization

### P-5. Consistency is mandatory

Từ ngữ, taxonomy provider, trạng thái key, preset mapping và runtime projection phải dùng cùng một semantics trên toàn flow.

Không được để mỗi lớp tự đặt lại khái niệm khác nhau cho cùng một đối tượng, ví dụ:

- UI gọi là `ElevenLabs`
- database lưu `11labs`
- runtime materialization lại map sang tên thứ ba

Consistency ở đây là requirement sản phẩm, không phải nice-to-have.

### P-6. Solid but simple

Không thêm kiến trúc nặng không cần thiết cho phase này.

Phase này cần:

- one clear owner: `editor8`
- one clear source of truth: `editor8` database
- one clear management surface: `editor8` UI

## 5. Scope

### In scope

- thêm UI trong `editor8` để quản lý keys
- lưu keys plain text trực tiếp trong database của `editor8`
- quản lý các key/provider configuration mà dự án hiện đang dùng
- bổ sung consistency rules giữa key records và preset/provider usage
- định nghĩa cách `editor8` materialize runtime config cho downstream execution

### Out of scope

- vault integration
- KMS envelope encryption ở application layer
- secret rotation automation với external providers
- enterprise-grade secret governance
- redesign `RenderRequest` để mang keys

## 6. Terminology And Scope Inventory

Để giữ tài liệu này nhất quán, các thuật ngữ sau phải được hiểu thống nhất từ đầu đến cuối:

- `key`: credential record có chứa secret material
- `preset`: non-secret runtime configuration record
- `materialization`: bước chuyển data từ database của `editor8` sang runtime-compatible form cho worker
- `source of truth`: database của `editor8`

Current-state inventory bắt buộc phải được cover như sau:

### 6.1 Dropbox

- Dropbox app key
- Dropbox app secret
- Dropbox refresh token

### 6.2 TTS

- Google Cloud service account key JSON
- ElevenLabs API key
- default TTS provider selection
- TTS preset definitions hiện đang ở `config/tts_presets.json`

### 6.3 Optional Runtime Auth Or Config That Should Be Considered In The Same Design

Không bắt buộc implement ở phase đầu nếu team muốn tách nhỏ, nhưng PRD phải design-compatible với các mục sau:

- `yt-dlp` cookies file semantics
- `yt-dlp` browser-cookie extraction settings
- future provider credentials khác của `editor8`

## 7. Functional Requirements

### F-1. Editor8 must provide a dedicated UI for key management

`editor8` phải có một màn hình quản lý keys/configurations chuyên biệt.

Tối thiểu user phải làm được:

- xem danh sách keys đã lưu
- tạo key mới
- cập nhật key hiện có
- bật/tắt key
- xoá key
- gắn label / mô tả / provider cho key

### F-2. Keys must be stored in editor8 database as plain text

Giá trị secret phải được lưu raw plain text trong database của `editor8`.

Điều này áp dụng cho phase hiện tại dù đây không phải long-term ideal security architecture.

### F-3. System must distinguish key records from provider presets

Hệ thống phải tách rõ hai khái niệm:

- **key record**
  - chứa secret material
  - ví dụ: Dropbox refresh token, ElevenLabs API key, Google service account JSON
- **preset/config record**
  - chứa non-secret runtime parameters
  - ví dụ: `voice_name`, `model_id`, `speaking_rate`, `stability`

Không được trộn mọi thứ vào một blob mơ hồ nếu điều đó làm mất khả năng validate consistency.

### F-4. System must support provider-specific key types

UI và database phải hỗ trợ ít nhất các loại sau:

- `dropbox_oauth_app`
- `google_cloud_service_account_json`
- `elevenlabs_api_key`

Mỗi type phải có validation phù hợp với shape dữ liệu của nó.

### F-5. Google Cloud JSON keys must be supported as structured text

Với Google Cloud service account, hệ thống phải cho phép paste toàn bộ JSON credential vào UI và lưu chính JSON đó dưới dạng plain text.

Không bắt buộc phải split thành từng field nhỏ trong database.

### F-6. Multi-key support must remain possible

Current codebase có round-robin multi-key semantics cho Google Cloud TTS và ElevenLabs.

Phase UI/database mới không được làm mất khả năng:

- lưu nhiều key cho cùng một provider
- chọn nhiều key active cùng lúc
- đánh dấu thứ tự hoặc policy group nếu sau này cần materialize rotation tương tự `KeyRing`

### F-7. Key status must be explicit

Mỗi key record tối thiểu phải có:

- `active`
- `inactive`
- `deleted` hoặc soft-delete equivalent

System không được rely vào file tồn tại hay không tồn tại để suy ra status.

### F-8. UI must support masked display while preserving retrievability

Vì phase này cần plain text storage, UI vẫn phải:

- mặc định mask secret khi hiển thị list/detail
- cho phép authorized user reveal hoặc copy raw value khi cần

Lưu ý:

- mask ở UI không thay đổi fact rằng DB vẫn lưu plain text
- requirement này chỉ để giảm accidental exposure trong vận hành

### F-9. Changes in UI must be auditable

Tối thiểu phải audit được:

- ai tạo key
- ai sửa key
- ai tắt key
- ai xoá key
- thời điểm thay đổi gần nhất

### F-10. Editor8 must materialize runtime configuration consistently

Do `maker8` hiện vẫn runtime theo env/file/path semantics, `editor8` phải có cơ chế materialize configuration từ DB sang runtime-compatible form.

PRD này không ép chốt implementation duy nhất, nhưng phải đảm bảo consistency cho một trong các hướng sau:

- `editor8` generate env/runtime config cho worker deployment
- `editor8` generate materialized files từ DB records trước khi worker start
- `editor8` expose an internal config projection service để worker bootstrap lấy config

Điều bắt buộc là:

- source of truth vẫn là database của `editor8`
- file chỉ còn là materialized runtime artifact, không còn là source of truth chính

### F-11. TTS presets must be manageable without editing repository files

Các preset hiện đang ở [`config/tts_presets.json`](../config/tts_presets.json) phải có đường đi sang UI/database management.

Tối thiểu phase này phải cho phép:

- xem preset definitions trong UI
- tạo/sửa preset
- gắn preset với provider type phù hợp

### F-12. Preset-to-key consistency must be validated

Ví dụ:

- preset `provider = elevenlabs` không được active nếu không có ít nhất một ElevenLabs key active
- preset `provider = google_cloud` không được active nếu không có Google key active hoặc approved ADC strategy
- Dropbox publishing path không được considered ready nếu thiếu Dropbox credentials active

### F-13. Secret values must not leak into render request payloads

`RenderRequest` Kafka payload không được chứa:

- raw API keys
- raw refresh tokens
- raw service account JSON

Nếu downstream cần biết config nào đang dùng, chỉ được truyền identifier/reference hoặc render-time metadata không chứa secret.

## 8. Data Model Requirements

## 8.1 Key Record

Hệ thống phải có một bảng hoặc entity tương đương cho key management.

Tối thiểu mỗi key record phải có:

- `id`
- `provider_type`
- `name`
- `description`
- `secret_value_plain_text`
- `status`
- `created_by`
- `updated_by`
- `created_at`
- `updated_at`

### Recommended extra fields

- `usage_scope`
- `rotation_group`
- `priority`
- `last_used_at`
- `last_validation_at`
- `notes`

## 8.2 Preset Record

Preset record phải tách khỏi key record.

Tối thiểu preset record phải có:

- `id`
- `preset_ref`
- `provider_type`
- `config_json`
- `status`
- `created_at`
- `updated_at`

## 8.3 Plain Text Storage Requirement

`secret_value_plain_text` phải lưu đúng raw value có thể dùng lại cho runtime:

- raw refresh token string
- raw API key string
- raw Google service account JSON string

Không dùng:

- hashing một chiều
- encryption bắt buộc ở application layer
- vault reference thay cho raw value

ở phase này.

## 9. UI Requirements

### 9.1 Key List Screen

Danh sách keys phải hiển thị tối thiểu:

- name
- provider type
- status
- last updated at
- updated by

### 9.2 Key Detail Screen

Detail phải cho phép:

- xem metadata
- reveal/copy plain text secret nếu user có quyền
- sửa metadata
- thay secret value
- bật/tắt key

### 9.3 Preset Screen

Preset management phải cho phép:

- xem preset list
- xem provider đang dùng
- sửa cấu hình non-secret
- validate preset với active keys hiện có

### 9.4 Validation Feedback

UI phải báo lỗi rõ khi:

- key format sai
- provider type không hợp lệ
- JSON key không parse được
- preset/provider mismatch
- secret required nhưng đang trống

## 10. Security And Access Requirements For This Phase

Phase này chấp nhận plain text database storage, nhưng vẫn phải có guardrails tối thiểu.

### Required controls

- RBAC hoặc permission checks cho màn hình key management
- masked display mặc định
- audit log cho CRUD actions
- không log raw secret vào application logs
- không trả raw secret trong list endpoints mặc định
- không serialize raw secret vào Kafka payloads, webhook payloads hoặc standard diagnostics

### Explicit non-goal

Phase này không yêu cầu:

- vault
- HSM
- secret envelope encryption
- zero-trust secret distribution

## 11. Consistency Requirements

Đây là yêu cầu bắt buộc của tài liệu này.

System sau khi làm xong phải maintain consistency ở 5 lớp:

### C-1. UI consistency

UI phải hiển thị cùng provider taxonomy với backend/database.

Không được để:

- UI gọi một kiểu provider
- database lưu kiểu khác
- runtime materialization dùng tên khác nữa

### C-2. Data consistency

Mỗi preset active phải map được sang provider type hợp lệ và key availability hợp lệ.

### C-3. Runtime consistency

Runtime config được materialize từ database phải phản ánh đúng active state trong UI.

Không được để:

- UI nói key đã tắt
- nhưng runtime vẫn tiếp tục dùng key cũ từ file stale

### C-4. Documentation consistency

Sau khi feature này được triển khai:

- README
- runbook
- setup guide
- architecture docs

phải đồng bộ với source of truth mới là UI + database của `editor8`.

Không được tiếp tục document file-based key management như con đường chính thức nếu hệ thống đã chuyển sang DB-backed management.

### C-5. Migration consistency

Trong thời gian migration, không được tồn tại hai nguồn vận hành song song mà không có rule ưu tiên rõ ràng.

Nếu file/env vẫn còn tồn tại tạm thời, hệ thống phải định nghĩa rõ:

- database hay file là source of truth
- ai được phép ghi vào nguồn nào
- runtime đọc từ nguồn nào
- khi nào dữ liệu được xem là stale

## 12. Migration Requirements

## 12.1 Phase 1: Introduce DB-backed source of truth

- thêm database tables/entities cho key records và preset records
- thêm UI CRUD cơ bản
- file-based config vẫn có thể tồn tại tạm thời như fallback hoặc seed source
- database phải được định nghĩa là source of truth mới ngay từ phase này nếu feature được bật

## 12.2 Phase 2: Materialize runtime from database

- `editor8` phải bắt đầu generate runtime-compatible configuration từ database
- file không còn là nơi operator sửa tay như source of truth chính

## 12.3 Phase 3: Remove operational dependence on manual file editing

- update docs
- update runbooks
- migration existing records từ file/env vào DB

## 13. Acceptance Criteria

PRD này được xem là đạt khi:

1. `editor8` có màn hình UI chính thức để quản lý keys.
2. Keys được lưu plain text trong database của `editor8`.
3. Ít nhất các loại credentials hiện dùng trong dự án được cover:
   - Dropbox
   - Google Cloud TTS JSON
   - ElevenLabs API key
4. TTS presets không còn phụ thuộc vào việc edit file repo thủ công như con đường chính thức.
5. `RenderRequest` gửi sang `maker8` vẫn không chứa raw secret material.
6. UI, database, preset mapping và runtime materialization giữ consistency với nhau.
7. Audit trail cho create/update/disable/delete key hoạt động.
8. UI mặc định mask secret values nhưng authorized user vẫn có thể reveal/copy.
9. Docs chính thức được cập nhật để phản ánh DB-backed key management là source of truth mới.
10. Trong thời gian migration, rule ưu tiên giữa DB và file/env được định nghĩa rõ và không tạo state mâu thuẫn.

## 14. Recommended Ownership

| Area | Primary owner | Notes |
|---|---|---|
| Key management UI | `editor8` owners | Đây là product/admin surface |
| Plain text database storage | `editor8` backend owners | Source of truth phase này |
| Runtime config materialization | `editor8` platform/backend owners | Bridge từ DB sang execution runtime |
| Render request contract | shared contract owners | Tiếp tục không chứa secret |
| `maker8` runtime consumption adjustments | `maker8` owners | Chỉ khi cần đổi bootstrap/materialization path |

## 15. Conclusion

Current project context cho thấy hệ thống vẫn đang thiên về file/env-based configuration cho các credentials quan trọng như Dropbox và TTS providers.

Yêu cầu mới phải chuyển ownership của key management sang `editor8` theo hướng:

- quản lý trên UI
- lưu plain text trong database của `editor8`
- giữ `RenderRequest` sạch khỏi secret
- maintain consistency giữa UI, DB, preset mapping và runtime behavior

Đây là bước đúng để hệ thống bớt phụ thuộc vào file editing thủ công, đồng thời vẫn giữ kiến trúc đủ simple cho phase hiện tại trước khi đầu tư vault ở phase sau.
