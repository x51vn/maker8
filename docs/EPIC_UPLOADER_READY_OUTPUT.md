# EPIC: Uploader-Ready Output Contract

**Source**: `EDITOR8_REQUEST_CHANGES_CARD_UPLOADER_READY_OUTPUT_AUDIT_2026-03-20.md`
**Created**: 2026-03-20
**Scope**: `render_contracts`, `editor8`, `maker8`

---

## Critical Review (Phản biện)

### Đồng ý

1. **`tags` / `hashtags` hiện tại là sentence-level** — đã xác nhận. `_collect_tags()` lấy nguyên `intent.key_points` (thường là câu dài do LLM sinh) và `scene.keywords` rồi gán thẳng vào `tags`. Đây thực sự là vấn đề semantic layer sai tầng.

2. **`thumbnail_ref` là raw URL ngoài** — đúng. `_pick_thumbnail()` chỉ lấy `candidates[0].url` — không có attribution, licensing, hay strategy nào.

3. **`credits` chỉ là `source_kind`** — đúng. `_collect_credits()` chỉ collect `{"http", "youtube"}` — vô dụng cho compliance audit.

4. **`publish.targets` có thể rỗng** — đúng. Không có cơ chế explicit nào phân biệt render-only vs publish-ready. Downstream phải đoán.

5. **Golden fixtures thiếu `uploader_metadata`** — đúng. Cả `golden_minimal_request.json` lẫn `golden_multiscene_request.json` đều không có `uploader_metadata`.

6. **`PublishTarget.metadata` quá free-form** — đúng. Hiện chỉ là `dict[str, Any]` — downstream không biết expect field nào.

### Phản biện

1. **Platform-specific text variants (Section 4.2) — over-scope cho editor8.** Editor8 là AI pipeline sinh content, không nên là nơi giữ logic format text cho YouTube vs TikTok vs Facebook. Per-platform text formatting thuộc adapter layer ở publisher worker. Thay vào đó, editor8 chỉ cần cung cấp semantic primitives đủ giàu (`title`, `short_title`, `summary`, `description`, `keywords`, `hashtags`) để adapter tự compose. **Counter-proposal**: Không thêm per-platform text fields vào `UploaderMetadata`. Giữ nó là common semantic layer. Per-platform override đã có `PublishTarget.metadata`.

2. **`variant` field (Section 4.3, 7.2) — có giá trị nhưng cần bound rõ.** Audit đề xuất `long_video | short_video | reel | social_post`. Canvas profile đã mang thông tin aspect ratio. `variant` bổ sung intent ("muốn upload dạng gì"), nhưng phải enforce là enum, không phải free string. **Accept với constraint**: `variant` phải là `Literal` enum trên `PublishTarget`, không phải trên common layer.

3. **Thumbnail tripartite split (Section 5.3, 7.6) — quá phức tạp cho giai đoạn hiện tại.** Tách `thumbnail_source_url` / `thumbnail_asset_ref` / `thumbnail_output_ref` giả định pipeline đã có thumbnail generation stage — mà hiện tại chưa có. **Counter-proposal**: Giai đoạn 1 chỉ cần `thumbnail_source_url` (URL gốc) + `thumbnail_strategy` (enum: `source_asset | auto_frame | none`). `thumbnail_asset_ref` sẽ do maker8 populate sau render nếu `strategy = auto_frame`.

4. **`canonical_url` / `cta_url` (Section 4.6) — hữu ích cho Facebook flow nhưng không nên bắt buộc.** Đây là optional field. Không phải mọi video đều có article URL. **Accept as optional**.

5. **`source_attributions[]` (Section 7.1) — đồng ý về hướng, nhưng phải pragmatic.** `AssetCandidate` hiện chỉ có `source_kind`, `url`, `title`, `metadata: dict`. Không có `creator`, `license`, `credit_text`. Muốn có attribution đúng phải enrich `AssetCandidate` ở provider level trước. **Accept nhưng phải làm 2 phase**: (a) enrich `AssetCandidate` với attribution fields, (b) propagate vào `UploaderMetadata.source_attributions`.

6. **5 golden fixtures per-platform (Section 7.7) — quá nhiều cho iteration 1.** Start với 2 fixture: render-only + YouTube publish-ready. Thêm TikTok/Facebook fixtures khi publisher worker thực sự implement platform đó.

7. **`content_rating` / `made_for_kids` / `scheduled_publish_at` normalization (Section 4.7) — mixed scope.** `content_rating` và `made_for_kids` thuộc publish policy, nên nằm ở `PublishTarget.params` per-platform, không phải common layer. `scheduled_publish_at` có thể ở cả common (default) và per-target (override). **Counter-proposal**: Giữ `content_rating` ở common layer là default. `made_for_kids` vào `PublishTarget.params`. `scheduled_publish_at` giữ ở common layer, cho phép per-target override.

### Tóm tắt scope được chấp nhận

| Audit Request | Accept? | Scope |
|---|---|---|
| Strengthen `UploaderMetadata` fields | **Yes, subset** | +`short_title`, +`summary`, +`keywords`, rename `tags`→`keywords`, +`thumbnail_source_url`, +`thumbnail_strategy`, +`source_attributions[]` |
| Normalize tags/hashtags | **Yes** | Enforce token-length, dedupe, sanitize |
| Add `variant` to `PublishTarget` | **Yes** | `Literal["long_video", "short_video", "reel", "social_post"]` |
| Add `enabled` to `PublishTarget` | **Yes** | Boolean flag |
| Make publish-readiness explicit | **Yes** | Check at assembler + validator level |
| Thumbnail strategy | **Partial** | `thumbnail_source_url` + `thumbnail_strategy` only (phase 1) |
| Source attributions | **Yes, 2-phase** | Enrich AssetCandidate first, then propagate |
| Platform-specific text variants in UploaderMetadata | **No** | Use `PublishTarget.metadata` for per-platform overrides |
| Tripartite thumbnail split | **No** | Phase 1 only needs source + strategy |
| 5 golden fixtures | **Partial** | 2 fixtures (render-only + YouTube), more later |
| `canonical_url` / `cta_url` | **Yes, optional** | Default `""` |

---

## Stories & Tasks

### Story 1: Expand `UploaderMetadata` Contract in `render_contracts`

**Estimate**: ~3h

#### Requirements

Extend `render_contracts/render_spec.py::UploaderMetadata` with new fields while maintaining backward compatibility (all new fields have defaults).

Fields to add:
- `short_title: str = ""` — short title for platforms with character limits (TikTok, Shorts)
- `summary: str = ""` — 1-2 sentence synopsis for Facebook/social post flows
- `keywords: list[str]` — replace semantic meaning of `tags` (normalized tokens); keep `tags` as deprecated alias
- `thumbnail_source_url: str = ""` — the actual origin URL of the thumbnail source image
- `thumbnail_strategy: str = "source_asset"` — enum-like: `source_asset | auto_frame | none`
- `source_attributions: list[SourceAttribution]` — replaces `credits`; keep `credits` as deprecated alias
- `canonical_url: str = ""` — article/source page URL (optional)
- `cta_url: str = ""` — call-to-action link (optional)

New model:
```python
class SourceAttribution(BaseModel):
    asset_ref: str = ""
    provider: str = ""
    source_url: str = ""
    creator: str = ""
    license: str = ""
    credit_text: str = ""
```

#### Context

- File: `editor8/backend/src/render_contracts/render_spec.py` AND `maker8/src/render_contracts/render_spec.py` — **MUST be identical**
- `UploaderMetadata` is consumed by: `editor8` assembler, `maker8` contracts/manifest, downstream uploader
- All new fields must have defaults to avoid breaking existing payloads
- `tags` and `credits` fields remain for backward compatibility but are considered deprecated
- `maker8/src/maker8/models/contracts.py` re-exports `UploaderMetadata` — verify `__all__` includes it

#### Acceptance Criteria

- [ ] `SourceAttribution` model defined in `render_contracts/render_spec.py`
- [ ] `UploaderMetadata` extended with all 8 new fields
- [ ] All new fields have sane defaults (empty string, empty list)
- [ ] `tags` and `credits` fields retained (backward compat)
- [ ] Both `editor8` and `maker8` copies of `render_spec.py` are **identical byte-for-byte**
- [ ] `maker8/models/contracts.py` `__all__` updated if needed
- [ ] Existing tests pass without modification (additive change)
- [ ] `ruff check` + `ruff format` pass on both projects
- [ ] mypy passes on editor8 (`mypy --strict`)

#### Verification

```bash
# editor8
cd editor8/backend && .venv/bin/ruff check src/render_contracts/ && .venv/bin/python -m mypy src/
# maker8
cd maker8 && venv/bin/ruff check src/render_contracts/ && venv/bin/python -m pytest tests/ -x -q
# Byte-identical check
diff editor8/backend/src/render_contracts/render_spec.py maker8/src/render_contracts/render_spec.py
```

**Drift prevention**: After editing `render_spec.py`, always `diff` both copies. Any divergence is a blocking bug.

---

### Story 2: Expand `PublishTarget` with `variant` and `enabled`

**Estimate**: ~2h

#### Requirements

Add two fields to `PublishTarget` in `render_contracts/render_spec.py`:

- `variant: str = ""` — content variant intent: `""` (unspecified), `long_video`, `short_video`, `reel`, `social_post`
- `enabled: bool = True` — allows disabling a target without removing it

#### Context

- `PublishTarget` is used in: `PublishConfig.targets`, `RenderResult.publish_targets`, `Manifest.publish_targets`
- `_build_publish_defaults()` in `editor8/api/routes.py` constructs `PublishTarget` dicts from channel DB rows — must wire `variant` from channel config
- `editor8/pipeline/assembler.py` validates targets via `PublishTarget.model_validate(t)` — no changes needed if fields have defaults
- `maker8/models/common.py` re-exports `PublishTarget` — verify `__all__`

#### Acceptance Criteria

- [ ] `variant` field added to `PublishTarget` with default `""`
- [ ] `enabled` field added to `PublishTarget` with default `True`
- [ ] Both copies of `render_spec.py` identical
- [ ] `_build_publish_defaults()` in `routes.py` sets `variant` from `channel.default_publish_config.get("variant", "")` if available
- [ ] Existing golden fixtures (`golden_multiscene_request.json`) still parse correctly
- [ ] All editor8 + maker8 tests pass
- [ ] `ruff check` + `mypy` clean on editor8

#### Verification

```bash
cd editor8/backend && .venv/bin/python -c "
from render_contracts.render_spec import PublishTarget
t = PublishTarget(platform='youtube', account_ref='yt:test')
assert t.variant == ''
assert t.enabled is True
print('OK')
"
diff editor8/backend/src/render_contracts/render_spec.py maker8/src/render_contracts/render_spec.py
cd editor8/backend && .venv/bin/python -m pytest tests/ -x -q
cd maker8 && venv/bin/python -m pytest tests/ -x -q
```

**Drift prevention**: `PublishTarget` is re-exported in `maker8/models/common.py`. Verify that `__all__` lists it. After any wire-format change, run test suites on **both** projects.

---

### Story 3: Normalize Tags & Hashtags in Metadata Builder

**Estimate**: ~3h

#### Requirements

Refactor `editor8/pipeline/metadata.py` to produce clean, token-level tags and hashtags:

1. **Tag normalization function** `_normalize_tag(raw: str) -> str | None`:
   - Strip whitespace and punctuation edges
   - Lowercase
   - Reject if len > 30 chars (sentence-like)
   - Reject if contains more than 3 words (phrase-like)
   - Return `None` if rejected

2. **`_collect_tags()`**: Apply `_normalize_tag()` to each candidate. Skip `None` results. Dedupe case-insensitive.

3. **Populate `keywords`** field with the normalized tag list (new `UploaderMetadata.keywords` from Story 1).

4. **`hashtags`**: Generate from `keywords`, not raw tags. Prefix `#`, strip spaces, CamelCase multi-word tags (e.g., `oil market` → `#OilMarket`).

5. **Keep `tags`** populated with same values as `keywords` for backward compat.

#### Context

- `_collect_tags()` aggregates from `intent.key_points` (LLM output, often sentences) and `storyboard.scenes[].keywords`
- LLM `key_points` are the primary source of sentence-length pollution
- `_MAX_TAGS = 15`, `_MAX_HASHTAGS = 5` — keep these limits
- `build_uploader_metadata()` returns a plain dict — update to include `keywords` key

#### Acceptance Criteria

- [ ] Tags with > 3 words or > 30 chars are rejected
- [ ] Tags are lowercased, stripped, deduplicated
- [ ] `keywords` field populated in returned dict
- [ ] `hashtags` are CamelCase social tokens, not raw sentences
- [ ] `tags` field still populated (== `keywords`) for backward compat
- [ ] Test `test_tags_deduplication` updated/extended
- [ ] New test: `test_sentence_tags_rejected` — verify sentence-length key_points are filtered
- [ ] New test: `test_hashtags_camelcase` — verify multi-word tags become CamelCase hashtags
- [ ] `ruff check` + `ruff format` pass
- [ ] All 634+ editor8 tests pass

#### Verification

```bash
cd editor8/backend
.venv/bin/python -m pytest tests/test_metadata.py -v
.venv/bin/python -m pytest tests/ -x -q
.venv/bin/ruff check src/editor8/pipeline/metadata.py tests/test_metadata.py
```

**Drift prevention**: `UploaderMetadata` field names must match between `render_contracts/render_spec.py` (schema) and `metadata.py` (builder output dict keys). If a field name is added/renamed in the schema, the builder must emit matching keys.

---

### Story 4: Build Source Attributions from Asset Data

**Estimate**: ~4h (2 sub-tasks)

#### Task 4a: Enrich `AssetCandidate` with Attribution Fields (~2h)

##### Requirements

Extend `AssetCandidate` dataclass in `editor8/assets/base.py`:

- `creator: str = ""` — author/channel name
- `license: str = ""` — license identifier (e.g., `"standard_youtube_license"`, `"pixabay_license"`, `"pexels_license"`)
- `credit_text: str = ""` — human-readable attribution string

Update providers to populate these fields where data is available:

| Provider | `creator` | `license` | `credit_text` |
|---|---|---|---|
| `pexels_provider` | `photo.photographer` | `"pexels_license"` | `"Photo by {photographer} on Pexels"` |
| `pixabay_provider` | `hit.user` | `"pixabay_license"` | `"Image by {user} on Pixabay"` |
| `ytdlp_provider` | `info.uploader` (if available) | `"standard_youtube_license"` | `"Source: {uploader} / YouTube"` |
| `icrawler_provider` | `""` (unknown) | `""` | `""` |
| `unsplash_provider` | photographer name if available | `"unsplash_license"` | `"Photo by {photographer} on Unsplash"` |

##### Context

- `AssetCandidate` is a `@dataclass` — additive fields with defaults won't break existing code
- Each provider's `search()` method constructs `AssetCandidate` objects — update the constructor calls
- Provider API responses differ — some include creator info, some don't
- Don't add API calls to fetch attribution — only use data already in search response

##### Acceptance Criteria

- [ ] `AssetCandidate` has 3 new fields with empty-string defaults
- [ ] Pexels provider populates `creator`, `license`, `credit_text`
- [ ] Pixabay provider populates `creator`, `license`, `credit_text`
- [ ] YouTube provider populates `creator`, `license`, `credit_text`
- [ ] `icrawler` and `unsplash` remain with empty strings (will be enriched later)
- [ ] Existing tests pass (additive, non-breaking)
- [ ] `ruff check` all modified provider files

#### Task 4b: Propagate Attributions into `UploaderMetadata` (~2h)

##### Requirements

Update `build_uploader_metadata()` to:

1. Replace `_collect_credits()` logic with `_collect_attributions()`:
   - For each scene's first asset, create a `SourceAttribution`-shaped dict from `AssetCandidate` fields
   - Deduplicate by `asset_ref`
   - Return `list[dict]` matching `SourceAttribution` schema

2. Populate both:
   - `source_attributions` — new list of attribution dicts
   - `credits` — legacy, derived from `[a["provider"] for a in source_attributions]` for backward compat

3. Update `thumbnail_source_url` — same as current `thumbnail_ref` (URL of first asset)

4. Set `thumbnail_strategy = "source_asset"` when thumbnail comes from an AssetCandidate; `"none"` when no assets.

##### Context

- `SourceAttribution` shape defined in Story 1 (or use dict matching schema)
- `build_uploader_metadata()` receives `scene_assets: dict[str, list[AssetCandidate]]`
- Each `AssetCandidate` now has `creator`, `license`, `credit_text` (from Task 4a)

##### Acceptance Criteria

- [ ] `source_attributions` populated in output dict
- [ ] Each attribution has: `asset_ref`, `provider`, `source_url`, `creator`, `license`, `credit_text`
- [ ] `credits` derived from attributions for backward compat
- [ ] `thumbnail_source_url` populated from first asset URL
- [ ] `thumbnail_strategy` set based on asset availability
- [ ] `thumbnail_ref` remains populated (backward compat)
- [ ] New test: `test_source_attributions_from_assets`
- [ ] New test: `test_thumbnail_strategy_set`
- [ ] All editor8 tests pass
- [ ] `ruff check` + `mypy` clean

#### Verification

```bash
cd editor8/backend
.venv/bin/python -m pytest tests/test_metadata.py tests/test_assets.py -v
.venv/bin/python -m pytest tests/ -x -q
.venv/bin/ruff check src/editor8/assets/ src/editor8/pipeline/metadata.py
.venv/bin/python -m mypy src/
```

**Drift prevention**: `SourceAttribution` dict keys produced by `metadata.py` must match field names in `render_contracts.render_spec.SourceAttribution`. Use the model's `.model_fields` for validation in tests.

---

### Story 5: Make Publish-Readiness Explicit

**Estimate**: ~3h

#### Requirements

1. **Add a computed property/function** to determine publish-readiness:
   - A `RenderRequest` is **publish-ready** if `render_spec.publish.targets` has at least one `enabled=True` target with a non-empty `platform` and `account_ref`
   - Otherwise it is **render-only**

2. **Add field to `RenderRequest`**: `publish_intent: str = "render_only"` — values: `"render_only"`, `"publish_ready"`

3. **Assembler sets `publish_intent`** automatically based on resolved targets.

4. **Validator warns** if `publish_intent == "publish_ready"` but `uploader_metadata.title` is empty or `uploader_metadata.keywords` is empty.

#### Context

- `assemble_render_request()` already constructs `PublishConfig` from `publish_defaults`
- `validator.py` has `validate_render_request()` returning `ValidationResult`
- `RenderRequest` is in `render_contracts/render_spec.py` — shared model
- Downstream can use `publish_intent` to route: render-only jobs skip upload queue

#### Acceptance Criteria

- [ ] `publish_intent` field on `RenderRequest` with default `"render_only"`
- [ ] Assembler sets to `"publish_ready"` when targets exist with `enabled=True`
- [ ] Validator produces warning (not error) when publish-ready but missing title/keywords
- [ ] Both copies of `render_spec.py` identical
- [ ] Golden fixtures updated: `golden_minimal_request.json` has `"publish_intent": "render_only"`
- [ ] `golden_multiscene_request.json` has `"publish_intent": "publish_ready"`
- [ ] All tests pass on both projects
- [ ] `ruff` + `mypy` clean

#### Verification

```bash
cd editor8/backend
.venv/bin/python -c "
from render_contracts.render_spec import RenderRequest
r = RenderRequest(job_id='test', render_spec={})
assert r.publish_intent == 'render_only'
print('OK')
"
.venv/bin/python -m pytest tests/test_assembler.py tests/test_validator.py -v
diff editor8/backend/src/render_contracts/render_spec.py maker8/src/render_contracts/render_spec.py
```

**Drift prevention**: `publish_intent` is a wire-format field. If maker8 needs to act on it (e.g., skip upload stage), update maker8 pipeline accordingly. Keep both `render_spec.py` files in sync.

---

### Story 6: Strengthen `_build_publish_defaults()` with platform minimum fields

**Estimate**: ~3h

#### Requirements

Enhance `_build_publish_defaults()` in `editor8/api/routes.py` to emit richer per-target metadata:

1. **Wire `variant`**: Read from `channel.default_publish_config.get("variant", "")` and set on target dict.

2. **Merge common metadata into per-target metadata**:
   - When assembler runs, `uploader_metadata` provides common `title`, `description`, `keywords`, `hashtags`
   - `_build_publish_defaults()` at API layer only provides channel routing info
   - At assembly time, **assembler should merge** common metadata into each target's `metadata` dict if not already overridden

3. **Platform-specific metadata defaults** in assembler:
   - YouTube targets: ensure `metadata` has `title`, `description`, `hash_tags`, `category`, `visibility`
   - TikTok targets: ensure `metadata` has `caption` (from `title`), `hashtags`, `visibility`
   - Facebook targets: ensure `metadata` has `title`, `summary`, `link`
   - Only fill gaps — never overwrite explicit values from `publish_defaults`

#### Context

- `_build_publish_defaults()` runs at API request time (has DB access, channel info)
- `assemble_render_request()` runs in pipeline (has AI artifacts, metadata)
- The merge must happen in assembler because that's where `uploader_metadata` is available
- Don't make `_build_publish_defaults()` depend on AI output — it runs before pipeline

#### Acceptance Criteria

- [ ] `variant` populated on each target from channel config
- [ ] Assembler merges common metadata into per-target `metadata` dicts
- [ ] YouTube targets get `title`, `description`, `hash_tags`, `category`, `visibility`
- [ ] TikTok targets get `caption`, `hashtags`, `visibility`
- [ ] Facebook targets get `title`, `summary`, `link`
- [ ] Explicit values from `publish_defaults` are never overwritten
- [ ] New test: `test_publish_target_metadata_merge_youtube`
- [ ] New test: `test_publish_target_metadata_merge_tiktok`
- [ ] New test: `test_publish_target_metadata_no_overwrite`
- [ ] All editor8 tests pass
- [ ] `ruff` + `mypy` clean

#### Verification

```bash
cd editor8/backend
.venv/bin/python -m pytest tests/test_assembler.py tests/test_api.py -v
.venv/bin/python -m pytest tests/ -x -q
.venv/bin/ruff check src/editor8/pipeline/assembler.py src/editor8/api/routes.py
```

**Drift prevention**: Platform-specific field names (`hash_tags`, `caption`, `summary`, `link`) must be documented in this story and match what xUploader expects. If xUploader changes field names, update assembler merge logic.

---

### Story 7: Add Uploader-Ready Golden Fixtures + Tests

**Estimate**: ~3h

#### Requirements

Create and test golden fixture files:

1. **`golden_render_only_request.json`** — based on existing minimal fixture but explicitly:
   - `publish_intent: "render_only"`
   - `publish.targets: []`
   - Has `uploader_metadata` with basic fields populated
   - No publish routing

2. **`golden_youtube_publish_ready.json`** — YouTube short video publish-ready:
   - `publish_intent: "publish_ready"`
   - `publish.targets[0]`: YouTube, `variant: "short_video"`, `enabled: true`
   - Per-target metadata: `title`, `description`, `hash_tags`, `category`, `visibility`
   - `uploader_metadata`: full common fields including `keywords`, `hashtags`, `source_attributions`, `thumbnail_source_url`, `thumbnail_strategy`

3. **Test class** `TestUploaderReadyFixtures` in `tests/test_golden_fixtures.py`:
   - Load each fixture → parse as `RenderRequest` → validate round-trip
   - Assert publish-readiness matches `publish_intent`
   - For publish-ready fixture: assert `uploader_metadata.keywords` non-empty, `source_attributions` present, `thumbnail_strategy` set
   - For publish-ready fixture: assert each target has `variant`, `enabled`, non-empty `metadata`

#### Context

- Existing fixtures: `tests/fixtures/golden_minimal_request.json`, `tests/fixtures/golden_multiscene_request.json`
- These fixtures serve as **contract lock tests** — if the schema changes and fixtures fail to parse, we catch drift immediately
- Fixtures must use `by_alias=True` wire format (e.g., `"in"` not `"in_"`)

#### Acceptance Criteria

- [ ] `golden_render_only_request.json` created, parseable as `RenderRequest`
- [ ] `golden_youtube_publish_ready.json` created, parseable as `RenderRequest`
- [ ] `TestUploaderReadyFixtures::test_render_only_fixture` — validates render-only semantics
- [ ] `TestUploaderReadyFixtures::test_youtube_publish_ready_fixture` — validates publish-ready semantics with keyword/attribution/thumbnail checks
- [ ] `test_golden_fixtures.py` imports from `render_contracts.render_spec` (not editor8-local copy)
- [ ] All editor8 tests pass
- [ ] `ruff` clean

#### Verification

```bash
cd editor8/backend
.venv/bin/python -m pytest tests/test_golden_fixtures.py -v
.venv/bin/python -m pytest tests/ -x -q
```

**Drift prevention**: Golden fixtures are the **definitive contract test**. If `render_contracts/render_spec.py` adds a required field without a default, fixture tests will fail — this is the intended early-warning mechanism. Never skip fixture tests.

---

### Story 8: Update `make_render_request()` Test Factory + Frontend Types

**Estimate**: ~3h

#### Requirements

##### 8a: Update `conftest.py` factory (~1.5h)

Update `make_render_request()` and `make_render_request_dict()` in `tests/conftest.py`:

- Include `uploader_metadata` with realistic values (title, keywords, hashtags, source_attributions)
- Include `publish_intent` field
- Add optional `publish_ready: bool = False` parameter that, when True, adds a YouTube publish target with filled metadata
- Ensure `make_render_request_dict()` serializes with `by_alias=True`

##### 8b: Update frontend TypeScript types (~1.5h)

Update `frontend/src/types/index.ts` to reflect new fields:

- `SourceAttribution` interface
- `UploaderMetadata` interface: add `short_title`, `summary`, `keywords`, `thumbnail_source_url`, `thumbnail_strategy`, `source_attributions`, `canonical_url`, `cta_url`
- `PublishTarget` interface: add `variant`, `enabled`
- `RenderRequest` interface: add `publish_intent`

#### Context

- `make_render_request()` is used by 237+ tests — any breaking change cascades
- Frontend types in `types/index.ts` must mirror backend Pydantic models
- Frontend `fetchAPI<T>()` relies on these types for response parsing

#### Acceptance Criteria

- [ ] `make_render_request()` includes `uploader_metadata` with realistic defaults
- [ ] `make_render_request(publish_ready=True)` includes YouTube publish target
- [ ] `make_render_request_dict()` output parseable by `RenderRequest.model_validate()`
- [ ] `SourceAttribution` TypeScript interface added
- [ ] `UploaderMetadata` TypeScript interface updated with all new fields
- [ ] `PublishTarget` TypeScript interface updated with `variant`, `enabled`
- [ ] `RenderRequest` TypeScript interface updated with `publish_intent`
- [ ] All backend tests pass (634+)
- [ ] Frontend builds without TypeScript errors (`npm run build`)
- [ ] `ruff` + `mypy` clean

#### Verification

```bash
# Backend
cd editor8/backend
.venv/bin/python -m pytest tests/ -x -q
.venv/bin/python -m mypy src/

# Frontend
cd editor8/frontend
npm run build
npm run test
```

**Drift prevention**: After changing any Pydantic model exposed via API, immediately update `frontend/src/types/index.ts`. Run `npm run build` to verify no TypeScript errors. The TypeScript types are the frontend's contract lock.

---

## Dependency Graph

```
Story 1 (UploaderMetadata contract)
  ├── Story 3 (Normalize tags) — needs `keywords` field
  ├── Story 4 (Source attributions) — needs `SourceAttribution` model
  ├── Story 5 (Publish readiness) — needs `publish_intent` field
  └── Story 8 (Factory + frontend types) — needs all new fields
Story 2 (PublishTarget variant/enabled)
  ├── Story 5 (Publish readiness) — uses `enabled` field
  ├── Story 6 (Platform metadata merge) — uses `variant` field
  └── Story 7 (Golden fixtures) — fixtures use `variant`, `enabled`
Story 3 ─── Story 7 (fixtures need normalized tags)
Story 4 ─── Story 7 (fixtures need attributions)
Story 6 ─── Story 7 (fixtures need merged platform metadata)
```

**Recommended execution order**:
1. Story 1 → Story 2 (contract layer — unblocks everything)
2. Story 3, Story 4a (parallel — both touch metadata builder)
3. Story 4b, Story 5, Story 6 (parallel-ish, depends on 1+2)
4. Story 7 → Story 8 (fixtures + factory + frontend — integration lock)

---

## Out of Scope (Deferred)

| Item | Reason |
|---|---|
| TikTok golden fixture | No TikTok publisher worker yet |
| Facebook golden fixture | No Facebook publisher worker yet |
| Thumbnail generation stage in maker8 | Separate epic; `thumbnail_strategy: auto_frame` is a forward-compatible placeholder |
| Platform-specific text formatting logic | Belongs in publisher adapter, not editor8 |
| `made_for_kids` field | Per-platform policy, belongs in `PublishTarget.params` when publisher implements it |
| Scheduled publish timezone validation | Requires publisher worker to be implemented first |
