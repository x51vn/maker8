# Kế Hoạch Thực Thi Key Management Tập Trung (editor8 + maker8)

Ngày: 2026-04-11  
Phạm vi tài liệu: thống nhất quản lý key tập trung cho `editor8` và `maker8`, trong đó:

- `editor8` quản lý key bằng UI
- `editor8` và `maker8` query trực tiếp key cần thiết trong database

## 0. Cơ sở và giả định

- Repo hiện tại là `maker8`; phần `editor8` được suy luận từ tài liệu liên repo trong `docs/`.
- Các key dưới đây được tổng hợp từ:
  - `src/maker8/config.py`
  - `.env.example`
  - `src/maker8/services/tts_client.py`
  - `docs/MAKE_PRODUCTION_READY.md`
  - `docs/EDITOR8_UI_KEY_MANAGEMENT_PLAIN_TEXT_DB_PRD_2026-04-10.md`
- Khi vào triển khai thật, cần một vòng xác nhận cuối với source code `editor8` để chốt tên biến/env chính thức.

## 1. Danh sách đầy đủ key cần quản lý tập trung

## 1.1 Nhóm bắt buộc (P0)

| Canonical key_id | Legacy env/file hiện tại | Consumer | Ghi chú |
|---|---|---|---|
| `dropbox.app_key` | `MAKER8_DROPBOX_APP_KEY`, `EDITOR8_DROPBOX_APP_KEY` | editor8, maker8 | Bắt buộc cho Dropbox OAuth app |
| `dropbox.app_secret` | `MAKER8_DROPBOX_APP_SECRET`, `EDITOR8_DROPBOX_APP_SECRET` | editor8, maker8 | Secret quan trọng |
| `dropbox.refresh_token` | `MAKER8_DROPBOX_REFRESH_TOKEN` | maker8 | Token upload runtime |
| `tts.google.service_account_json` (multi-key) | `gg-tts-keys/*.json`, `MAKER8_GOOGLE_TTS_KEYS_DIR` | maker8 | Round-robin per video |
| `tts.google.adc_service_account_json` (fallback) | `GOOGLE_APPLICATION_CREDENTIALS`, `MAKER8_GOOGLE_APPLICATION_CREDENTIALS` | maker8 | Fallback khi không dùng key ring |
| `tts.elevenlabs.api_key` (multi-key) | `elevenlabs-keys/*.txt|*.key`, `MAKER8_ELEVENLABS_KEYS_DIR` | maker8 | Round-robin per video |
| `tts.elevenlabs.api_key_single` | `MAKER8_ELEVENLABS_API_KEY` | maker8 | Fallback single-key |
| `kafka.maker8.username` | `MAKER8_KAFKA_USERNAME` | maker8 | Optional theo hạ tầng, nhưng phải quản lý tập trung nếu dùng SASL |
| `kafka.maker8.password` | `MAKER8_KAFKA_PASSWORD` | maker8 | Secret bắt buộc khi dùng SASL |
| `kafka.editor8.username` | (xác nhận từ repo editor8) | editor8 | Nên tách scope với maker8 |
| `kafka.editor8.password` | (xác nhận từ repo editor8) | editor8 | Secret bắt buộc nếu editor8 có producer/consumer SASL |
| `llm.editor8.api_key` | `EDITOR8_LLM_API_KEY` | editor8 | Key gọi LLM gateway/upstream |
| `media.pexels.api_key` | `EDITOR8_PEXELS_API_KEY` | editor8 | Stock media bắt buộc trong thực tế vận hành hiện tại |
| `auth.editor8.jwt_secret` | (nêu trong tài liệu production-ready) | editor8 | Secret ký JWT |
| `auth.editor8.admin_password` | (nêu trong tài liệu production-ready) | editor8 | Secret quản trị |

## 1.2 Nhóm nên quản lý cùng hệ thống (P1/P2)

| Canonical key_id | Legacy env/file hiện tại | Consumer | Ghi chú |
|---|---|---|---|
| `media.unsplash.api_key` | `EDITOR8_UNSPLASH_API_KEY` | editor8 | Optional provider |
| `media.pixabay.api_key` | `EDITOR8_PIXABAY_API_KEY` | editor8 | Optional provider |
| `search.brave.api_key` | `EDITOR8_BRAVE_API_KEY` | editor8 | Optional provider |
| `search.serpapi.api_key` | `EDITOR8_SERPAPI_KEY` | editor8 | Optional provider |
| `ytdlp.cookies` | `MAKER8_YTDLP_COOKIES_FILE`, `MAKER8_YTDLP_COOKIES_FROM_BROWSER` | maker8 | Auth material dạng file/blob, không phải API key nhưng cần quản trị giống secret |

## 1.3 Không lưu trong key DB (non-secret hoặc bootstrap paradox)

| Mục | Lý do |
|---|---|
| `EDITOR8_LLM_BASE_URL`, `EDITOR8_LLM_MODEL`, `EDITOR8_DROPBOX_REDIRECT_URI` | Không phải secret |
| Topic names, timeout, logging configs | Không phải key |
| DB password dùng để truy cập chính key database | Tránh bootstrap paradox; nên lưu ở hạ tầng (secret manager/deploy env) |

## 2. Quy trình 1: Mở rộng tìm kiếm các vấn đề có thể gặp

Mục tiêu bước này là mở rộng tối đa failure modes, chưa thu gọn sớm.

## 2.1 Nhóm dữ liệu và mô hình

1. Không có taxonomy chung (`dropbox`, `Dropbox`, `dbx`) gây drift UI/DB/runtime.
2. Không tách `key record` và `preset record` dẫn đến validate yếu, khó audit.
3. Không hỗ trợ multi-key cho Google/ElevenLabs làm mất rotation semantics hiện tại.
4. Thiếu versioning key khiến rollback khó và có thể đọc nhầm key cũ.
5. Không có priority/weight/rotation_group nên không thể kiểm soát chọn key runtime.

## 2.2 Nhóm runtime và integration

1. `maker8` query DB trực tiếp nhưng không có cache + invalidation, dễ tăng tải DB.
2. Có cache nhưng không có cơ chế bust-cache, gây dùng stale key sau khi revoke.
3. Hai service cùng query DB nhưng dùng rule lọc khác nhau (`active`, `deleted`) gây hành vi lệch.
4. Disable key tại UI trong lúc job đang chạy gây race condition nếu không snapshot theo job.
5. Startup của `maker8` không fail-fast khi thiếu key bắt buộc -> tiêu thụ job rồi fail hàng loạt.

## 2.3 Nhóm migration và cutover

1. Vừa đọc env/file vừa đọc DB nhưng không có precedence rule rõ ràng -> trạng thái mâu thuẫn.
2. Import từ `gg-tts-keys/` và `elevenlabs-keys/` vào DB không idempotent -> duplicate records.
3. Không có kế hoạch rollback nếu migration lỗi.
4. Cập nhật docs/runbook chậm hơn code -> vận hành theo hướng dẫn cũ.

## 2.4 Nhóm bảo mật và tuân thủ pha plain-text

1. Plain-text DB làm tăng blast radius khi rò rỉ dump/backup.
2. Log vô tình in raw secret (debug/exception) trong editor8 hoặc maker8.
3. API list keys trả thẳng raw secret cho user không đủ quyền.
4. Thiếu audit trail (ai sửa key nào, lúc nào) gây mất forensic.
5. Quyền DB của `maker8` quá rộng (write/delete) thay vì read-only.

## 2.5 Nhóm kiểm thử và chất lượng

1. Thiếu contract test cross-repo cho schema key store.
2. Thiếu e2e test cho path: UI update key -> maker8 dùng key mới.
3. Thiếu chaos test cho revoke key giữa chừng và fallback key.
4. Thiếu test masking/reveal permission ở UI.

## 3. Quy trình 2: Phản biện, thu gọn vấn đề chính và sắp xếp ưu tiên

## 3.1 Vấn đề cốt lõi sau phản biện

Sau khi thu gọn, các rủi ro chính chi phối thành công triển khai chỉ còn 6 cụm:

1. `Single source of truth` + precedence trong giai đoạn migration.
2. `Schema chuẩn` hỗ trợ multi-key + preset consistency.
3. `Read path của maker8` (query trực tiếp DB) phải fail-fast và không stale.
4. `RBAC + masking + audit` để giảm rủi ro plain-text.
5. `Cutover strategy` có rollback rõ ràng.
6. `Cross-repo contract tests` để ngăn drift.

## 3.2 Ưu tiên

### P0 (bắt buộc trước khi bật production)

1. Chốt taxonomy + schema key/preset/binding thống nhất.
2. Chốt precedence rule: DB là source of truth khi feature flag bật.
3. Maker8 read-only DB query path + readiness fail-fast.
4. RBAC + masking + audit log tối thiểu cho key UI/API.
5. Migration tool idempotent từ env/file -> DB.

### P1 (ngay sau P0)

1. Cache + invalidation theo version counter.
2. Rotation policy rõ cho Google/ElevenLabs (priority + round-robin group).
3. E2E test liên repo cho update/revoke key realtime.

### P2 (mở rộng)

1. Onboard toàn bộ optional providers (Unsplash/Pixabay/Brave/SerpAPI).
2. Dashboard health/key freshness.

## 4. Kế hoạch thực thi

## 4.1 Thiết kế dữ liệu (editor8 DB)

Đề xuất bảng tối thiểu:

- `credential_keys`
  - `id`, `key_id`, `provider_type`, `secret_value_plain_text`, `status`
  - `scope` (`editor8`, `maker8`, `shared`)
  - `priority`, `rotation_group`
  - `created_by`, `updated_by`, `created_at`, `updated_at`
- `credential_presets`
  - `id`, `preset_ref`, `provider_type`, `config_json`, `status`
- `credential_bindings`
  - map use-case -> key selection (ví dụ `maker8.tts.google`, `maker8.dropbox.upload`)
- `credential_audit_logs`
  - action CRUD, actor, before/after metadata (không log raw secret)
- `credential_runtime_version`
  - một counter tăng dần để invalidate cache giữa services

## 4.2 Query model cho editor8 và maker8 (direct DB query)

- `editor8`: read/write đầy đủ qua backend service + UI.
- `maker8`: read-only role, chỉ được `SELECT` trên bảng credentials cần cho runtime.
- Mỗi job của maker8 lấy snapshot key theo `runtime_version` tại lúc bắt đầu job.
- Nếu `runtime_version` đổi trong lúc job chạy: không đổi key giữa chừng; áp dụng cho job kế tiếp.

## 4.3 Lộ trình theo phase

### Phase A - Chuẩn hóa (1-2 ngày)

1. Chốt danh sách key chính thức (file này) và owner từng key.
2. Chốt key taxonomy + naming convention.
3. Chốt precedence rule migration.

### Phase B - DB + Backend editor8 (3-5 ngày)

1. Tạo migrations cho 4 bảng credential.
2. API CRUD key/preset/binding + validations theo provider type.
3. RBAC + masking + audit log.

### Phase C - UI editor8 (3-5 ngày)

1. Màn hình key list/detail/reveal/disable.
2. Màn hình preset + validate dependency key.
3. Cảnh báo readiness theo key bắt buộc.

### Phase D - Maker8 direct DB query (3-4 ngày)

1. Thêm config DB read-only cho maker8.
2. Thay lớp nạp credential từ env/file sang DB query (giữ fallback bằng feature flag).
3. Fail-fast startup nếu thiếu key bắt buộc (`dropbox`, provider TTS đã chọn, kafka auth nếu SASL bật).

### Phase E - Migration + Cutover (2-3 ngày)

1. Script import idempotent từ env/file hiện tại:
   - Dropbox env
   - Google JSON key dir
   - ElevenLabs key dir/env
2. Dry-run trên staging.
3. Bật feature flag theo từng môi trường, theo dõi lỗi, có rollback plan.

### Phase F - Ổn định vận hành (2-3 ngày)

1. Cập nhật README/runbook/ops docs.
2. Bổ sung contract test + e2e test liên repo.
3. Bổ sung dashboard audit/readiness.

## 4.4 Tiêu chí nghiệm thu

1. `editor8` quản lý toàn bộ key qua UI (CRUD + mask/reveal + audit).
2. Keys được lưu plain text trong DB theo yêu cầu phase hiện tại.
3. `editor8` và `maker8` đều query trực tiếp DB; không phụ thuộc file/env như source chính.
4. `RenderRequest` qua Kafka không chứa raw secret.
5. Maker8 fail-fast khi thiếu key bắt buộc, không consume job trong trạng thái thiếu credential.
6. Migration có rollback rõ và không mất dữ liệu key.

## 5. Quyết định bắt buộc cần chốt sớm

1. Có tách key Kafka giữa `editor8` và `maker8` hay dùng chung.
2. Có chấp nhận giữ fallback env/file sau cutover hay cắt hoàn toàn.
3. Rotation policy chuẩn cho multi-key: strict round-robin hay weighted priority.
4. Retention policy cho audit log và soft-delete key.

## 6. Kết luận

Với trạng thái code/tài liệu hiện tại, hệ thống đã có đủ bằng chứng để chuyển sang mô hình:

- `editor8` là owner của key management UI
- DB của `editor8` là source of truth
- `editor8` + `maker8` query trực tiếp key cần thiết từ DB

Trọng tâm thành công không nằm ở UI form nhập key, mà ở 4 điểm: schema đúng, migration sạch, maker8 read-path ổn định, và guardrails bảo mật tối thiểu cho plain-text phase.
