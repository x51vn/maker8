# Đánh giá nhanh: editor8 (summary for maker8 boundary)

Mục tiêu: tóm tắt những vấn đề quan trọng khi review `editor8` từ góc nhìn consumer `maker8`, liệt kê rủi ro interface/operational, và đề xuất hành động ưu tiên. Tài liệu này xây trên các ghi chú và fixture trong repository maker8.

- Tình trạng hiện tại: `editor8` là producer của các render request; maker8 tiêu thụ theo contract trong `render_contracts/`.
- Risk trọng yếu: interface drift giữa `editor8` và `maker8` (fields mới, semantics khác), secrets bị commit, deploy/health drift, và thiếu cross-repo contract tests.
- Priority actions (ngắn gọn): 1) Add cross-repo contract tests/fixtures, 2) Enforce managed binary/updater pattern (yt-dlp), 3) Fix OAuth/deploy health & worker startup, 4) Rotate/purge leaked secrets.

Chi tiết phát hiện

- Contract / schema
  - `render_contracts/render_spec.py` là source of truth; nhiều docs ghi rõ editor8 phải xuất payload tương thích. Tuy nhiên có evidence drift: tests include `tests/fixtures/golden_editor8_full_request.json` to validate parsing in maker8 — giữ và mở rộng test này thành CI cross-repo.
  - Recommendation: tạo job CI (or GH Action) chạy fixture-based parse test against latest editor8 artifact (or PR) để catch drift sớm.

- Binary management (yt-dlp)
  - Requirement: editor8 cần mirror maker8 pattern: managed binary path, startup logging of active path+version, scheduled updater. Docs `docs/MAKER8_EDITOR8_MEDIA_QUALITY_REQUESTS_2026-04-07.md` chỉ rõ.
  - Recommendation: extract `YtdlpUpdater` thành shared module or copy pattern; log path/version at startup; provide a health metric for updater status.

- Search / Quality enforcement
  - Editor8 must prefilter YouTube candidates to duration < 600s and probe metadata when providers omit duration. See media quality doc lines about prefilter.
  - Recommendation: enforce filter at search layer and when materializing assets set `max_duration_sec` metadata in RenderRequest.

- Deploy / Health / Worker
  - Docs show mismatch: README and DEPLOY.md omit worker start; healthcheck semantics drift between maker8 and editor8. This breaks operational runbooks and monitoring.
  - Recommendation: update editor8 deploy docs to require backend + worker + frontend; implement consistent live/ready/status semantics and add automated healthcheck tests.

- OAuth / Dropbox flow
  - Current flow lacks proper PKCE/state verification and has frontend/backend mismatch for callback handling; risk of incomplete OAuth and insecure flows.
  - Recommendation: implement PKCE or server-side state verification, add tests for `/api/dropbox/connect` and callback, document redirect URIs and session handling.

- Secrets exposure
  - Critical: repo notes show secrets committed (service-account JSONs, .env files). Before go-live must rotate keys and purge git history.
  - Recommendation (urgent): rotate Google SA keys, Dropbox secrets, Kafka passwords, LLM keys, JWT secrets; run git filter-repo or BFG and coordinate with infra to revoke old credentials.

- Operational runbook and ownership
  - Maker8 ops docs expect `render_contracts/` sync and cross-repo testing; verify owner of fields (which side populates attribution, duration, etc.).
  - Recommendation: assign explicit ownership for each contract field in `CONTRACT_FIELD_STATUS.md` and add a checklist to release PRs that touch contracts.

Next steps (practical)
1. Add a new CI job in editor8 repo that runs maker8's contract parse tests using `tests/fixtures/golden_editor8_full_request.json` (or equivalent golden payload). This prevents schema drift.  
2. Implement/replicate `YtdlpUpdater` from maker8 into editor8; add startup logging + scheduled update + health endpoint.  
3. Fix Dropbox OAuth to use PKCE/state and add endpoint tests.  
4. Rotate leaked secrets immediately and publish a migration checklist for owners.  
5. Update DEPLOY.md and runbook so operator starts backend+worker+frontend; add automated smoke test that asserts worker processes are connected (e.g. health endpoints respond).

References in this repo

- `src/render_contracts/render_spec.py`  
- `tests/fixtures/golden_editor8_full_request.json`  
- `docs/MAKER8_EDITOR8_MEDIA_QUALITY_REQUESTS_2026-04-07.md`  
- `docs/MAKE_PRODUCTION_READY.md`  
- `CONTRACT_FIELD_STATUS.md`  

Tóm lại: editor8 yêu cầu hành động cross-repo để đảm bảo contract compatibility, operational correctness, và an toàn secrets. Ưu tiên: secrets rotation + contract CI + deploy/health fixes.
