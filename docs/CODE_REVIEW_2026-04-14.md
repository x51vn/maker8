# Maker8 Code Review

**Date:** 2026-04-14
**Branch:** `feature/XST-1068-uploader-identity-cleanup`
**Reviewer:** Claude (automated)

---

## Executive Summary

Maker8 is a well-architected video render pipeline worker with solid design patterns: structured logging, graceful degradation, retry with exponential backoff, dead-letter queue routing, and a clean stage-based pipeline. The codebase is consistent and production-hardened in many areas. However, there are notable resource management issues, test coverage gaps, and some latent bugs worth addressing.

**Severity legend:** CRITICAL = production risk, HIGH = should fix soon, MEDIUM = tech debt, LOW = nice to have

---

## CRITICAL Findings

### C1. TTS client creates new HTTP/gRPC clients per call
**File:** `src/maker8/services/tts_client.py` (lines 294-298, GoogleCloudTTSProvider._build_client)
A new `ElevenLabs()` HTTP client and a new Google Cloud gRPC client are instantiated on **every `synthesize()` call** (i.e., per scene). Connection pooling is lost entirely. For a 10-scene video, this means 10 separate TCP/TLS handshakes. Under load or with high-latency networks, this significantly increases TTS stage duration and risks timeout.

**Recommendation:** Cache clients per provider instance, reconnect only on credential rotation.

### C2. ThreadPoolExecutor created per scene synthesis
**File:** `src/maker8/services/tts_client.py` (line 609, `_invoke` inner function)
A new `ThreadPoolExecutor` is spun up for every single scene's TTS call as a timeout backstop. Thread pool creation/teardown overhead per scene is wasteful. Worse, the thread-based timeout **cannot actually cancel** a blocked network call -- the thread continues executing as a zombie even after `TimeoutError` is raised.

**Recommendation:** Use a single shared executor for the TTSService lifetime, or switch to `signal.alarm` / async timeout.

### C3. Dropbox session upload has no abort on failure
**File:** `src/maker8/services/dropbox_client.py`
If a chunked (session-based) upload fails partway through, the Dropbox server-side session is left dangling. There is no `upload_session_abort` call in any error path. Dangling sessions count against Dropbox API quotas.

**Recommendation:** Add `finally` block calling `files_upload_session_finish` or abort on exception.

---

## HIGH Findings

### H1. Falsy-value bug in scene_detect options
**File:** `src/maker8/pipeline/scene_detect.py` (line ~129)
```python
threshold = (options.scene_detect_threshold if options else None) or _DEFAULT_THRESHOLD
```
The `or _DEFAULT_THRESHOLD` pattern means a **valid value of `0.0`** (detect every frame) silently becomes `0.35`. This is a real bug if anyone sets threshold to 0.

**Recommendation:** Use explicit `if x is None` checks instead of `or` for numeric defaults.

### H2. Retry delay logging mismatch
**File:** `src/maker8/pipeline/orchestrator.py` (lines 295 vs 302)
`policy.delay(attempt)` is called for logging, then `policy.sleep(attempt)` calls `delay()` again internally. Due to random jitter, the **logged delay will not match the actual sleep duration**.

**Recommendation:** Compute delay once, log it, then sleep for that exact value.

### H3. Credential files never cleaned up
**File:** `src/maker8/services/tts_client.py` (`_load_google_key_ring_from_db`)
Service-account JSON files are written to `settings.work_dir / "credentials" / "google"` but never deleted. Across process restarts, stale credential files accumulate on disk indefinitely.

**Recommendation:** Clean up credential directory on startup or use `tempfile.NamedTemporaryFile`.

### H4. No connect timeout on PostgreSQL credential reader
**File:** `src/maker8/services/credential_reader.py` (line ~161)
`psycopg2.connect(url)` has no `connect_timeout` parameter. If the DB host is unreachable, the call blocks for the OS-default TCP timeout (often 2+ minutes), **holding `_lock`** and stalling all other credential reads.

**Recommendation:** Add `connect_timeout=10` to the connection string or parameters.

### H5. YouTube connector: unhandled JSON parse failure
**File:** `src/maker8/plugins/sources/youtube.py` (line 348)
`json.loads(result.stdout)` after a successful `yt-dlp` resolve has no try/except. If yt-dlp emits non-JSON to stdout (warning lines, debug output), this raises `JSONDecodeError` with no structured error code.

**Recommendation:** Wrap in try/except and classify as non-retryable error.

### H6. Download extension allowlist too narrow
**File:** `src/maker8/plugins/sources/youtube.py` (lines 422-425)
The download method only searches for `mp4`, `mkv`, `webm` extensions. If `yt-dlp` produces `flv`, `avi`, `ts`, or other formats, the download is reported as failed even though the file exists on disk.

**Recommendation:** Glob for any video file in the output directory, or use yt-dlp's `--print filename` option.

### H7. Validate stage mutates spec during validation
**File:** `src/maker8/pipeline/validate.py` (lines 228-229)
The validation stage **mutates** `layer.role` and `layer.required` during V2 validation (primary_visual inference). A validation stage should be side-effect-free. This mutation could cause confusing behavior if validation is ever re-run or the spec is inspected post-validation.

**Recommendation:** Return inferred values separately or apply mutations in a dedicated normalization step after validation.

---

## MEDIUM Findings

### M1. Normalize stage: massive code duplication
**File:** `src/maker8/pipeline/normalize.py` (868 lines)
`_normalize_video`, `_normalize_video_sw`, and `_normalize_video_cpu_decode_nvenc` are three nearly-identical ~80-line methods differing only in FFmpeg command and fallback behavior. A bug fix must be applied in three places.

**Recommendation:** Extract a common `_run_ffmpeg_normalize(cmd, fallback_fn)` method.

### M2. No Pydantic validators on wire-format models
**File:** `src/render_contracts/render_spec.py`
No `field_validator` or `model_validator` is defined on any model. Invalid values pass construction:
- `Canvas.fps = 0` or negative
- `Scene.duration` negative
- `OutputConfig.bitrate = ""`

All validation is deferred to the pipeline's ValidateStage. A malformed spec that bypasses the pipeline (e.g., direct model use in tests or tooling) gets no safety net.

**Recommendation:** Add basic validators for obviously-invalid values (fps > 0, duration >= 0).

### M3. PipelineContext is a God object
**File:** `src/maker8/pipeline/context.py`
~30 mutable fields that every stage reads from and writes to. The `resolved_plans` field is typed as `dict[str, Any]` with a comment saying it holds `ResolvedAssetPlan` -- losing type safety entirely.

**Recommendation:** Type `resolved_plans` properly. Consider grouping related fields into sub-objects (e.g., `DownloadState`, `TTSState`) to reduce surface area.

### M4. Error codes are free-form strings
Error codes like `"FFMPEG_ERROR"`, `"RENDER_FAILED"`, `"SCENE_DETECT_FFMPEG_ERROR"` are scattered as string literals across the codebase with no centralized enum or registry. This makes it impossible to guarantee uniqueness or provide documentation for DLQ/result consumers.

**Recommendation:** Create an `ErrorCode` enum or at minimum a constants module.

### M5. Simple upload reads entire file into memory
**File:** `src/maker8/services/dropbox_client.py` (line 152)
`_simple_upload` calls `local_path.read_bytes()` which allocates up to 150 MiB in a single bytes object for files just under the threshold.

**Recommendation:** Use a file handle instead: `with open(path, 'rb') as f: dbx.files_upload(f, ...)`.

### M6. `_STAGE_ORDINALS` sync risk with RenderStage enum
**File:** `src/maker8/observability/metrics.py` (line 197)
The `_STAGE_ORDINALS` dict duplicates `RenderStage` values as string keys. If a new stage is added to the enum but not to this dict, `set_current_stage` silently returns 0.

**Recommendation:** Derive ordinals from the enum definition: `{s.value: i for i, s in enumerate(RenderStage, 1)}`.

### M7. Database connection per cache refresh
**File:** `src/maker8/services/credential_reader.py`
Opens a new PostgreSQL connection on every 60-second cache refresh. Each refresh = TCP connect + optional TLS + query + close.

**Recommendation:** Use a persistent connection with reconnect-on-error, or a lightweight connection pool.

### M8. `build_remote_path` has unused `job_id` parameter
**File:** `src/maker8/services/dropbox_client.py`
The `job_id` parameter is accepted but never used in path construction. Misleading for callers.

**Recommendation:** Remove the parameter or use it in the path.

---

## LOW Findings

### L1. No shared conftest.py -- duplicated test helpers
Helper factories (`_make_spec`, `_make_ctx`, `_make_settings`) are reimplemented in nearly every test file with slight variations. A project-level `conftest.py` would reduce ~200 lines of duplication.

### L2. Source-code-inspection tests are fragile
`test_tts_startup.py` reads `app.py` source at test-time and inspects line proximity. `test_scene_detect.py` uses `inspect.getsource` on Orchestrator. These break on refactoring.

### L3. prometheus_client stub duplicated across test files
Both `test_youtube_format.py` and `test_ytdlp_updater.py` duplicate the same prometheus_client module stub. Should be in conftest.py.

### L4. Account email logged at INFO level
**File:** `src/maker8/services/dropbox_client.py` (line 73-74)
`email=account.email` is logged at INFO level. Depending on log aggregation policies, this could expose PII.

### L5. Hardcoded Dropbox client timeout
**File:** `src/maker8/services/dropbox_client.py` (line 64)
`timeout=300` (5 minutes) is not configurable via `Settings`.

### L6. `is_degraded` triggers on informational warnings
**File:** `src/maker8/pipeline/context.py` (line 134)
`is_degraded` returns True whenever `self.warnings` is non-empty. Some warnings (e.g., `SCENE_DETECT_EMPTY`) are informational and do not indicate actual degradation.

### L7. `_classify_value_error` uses fragile substring matching
**File:** `src/maker8/pipeline/resolve.py` (line 262)
Error classification matches substrings in exception messages. If upstream connectors change their error messages, classification silently breaks.

### L8. Broad `except Exception` in orchestrator wraps transient errors as non-retryable
**File:** `src/maker8/pipeline/orchestrator.py` (line 304)
Any unexpected exception (e.g., temporary `IOError`) is wrapped into a non-retryable `StageError`, preventing retry.

### L9. `test_validate_identity.py` declares `caplog` but never asserts on it
The test claims to verify warnings but only checks "does not raise" -- actual warning content is unverified.

### L10. Environment-dependent test
`test_ffmpeg_runtime.py::test_system_binary_fallback` has conditional assertions based on whether `/usr/bin/ffmpeg` exists on the host.

---

## Test Coverage Assessment

### Well-Tested Areas
- Contract/model validation and round-trip fidelity (excellent)
- Scene detection parsing and post-processing (excellent)
- Pipeline degradation and survivability (excellent)
- Encoder detection and FFmpeg command building (excellent)
- YouTube error classification (excellent)
- V2 spec features (roles, policies, subtitles) (excellent)

### Completely Untested (28 modules)
| Category | Untested Modules |
|---|---|
| **Infrastructure** | `app.py`, `config.py`, `kafka/producer.py` |
| **Services** | `dropbox_client.py`, `credential_reader.py`, `key_ring.py` |
| **Effects plugins** | `fade`, `zoom_pan`, `blur`, `brightness_contrast`, `slide`, `color_overlay`, `grayscale`, `rotate`, `mirror`, `chroma_key` (6 of 10 untested) |
| **Rendering core** | `composer.py`, `layers.py`, `perf_profile.py` |
| **Pipeline stages** | `upload.py` (Dropbox upload), `stage.py` (ABC) |
| **Utilities** | `canon.py`, `color.py`, `logging.py`, `hashing.py`, `versions.py` |
| **Models** | `manifest.py` |
| **Contracts** | `events.py` |
| **Plugins** | `base.py`, `registry.py`, `http_source.py` |

### Under-Tested
- **Orchestrator**: retry loop, full error recovery flow, DLQ production
- **Render stage**: actual video composition integration
- **TTS client**: full synthesize flow, timeout behavior
- **Normalize stage**: actual subprocess execution, error handling paths

---

## Architecture Observations

### Strengths
1. **Clean pipeline pattern** -- 9 stages with clear single responsibilities
2. **Graceful degradation** -- `PARTIAL` status for partial failures, per-asset/scene isolation
3. **Structured observability** -- structlog + Prometheus + file-based health probes
4. **Centralized credential management** -- DB-backed credential reader with TTL cache
5. **Plugin-based extensibility** -- source connectors and effects are pluggable
6. **Strong contract testing** -- golden fixtures ensure wire-format stability

### Concerns
1. **Single-threaded design** limits throughput to 1 job at a time per worker
2. **No end-to-end integration test** from Kafka message to rendered video
3. **render_contracts is a shared package** but lives inside maker8's repo -- changes require coordinated deployment with editor8
4. **Vietnamese-locale defaults** baked into models (`lang="vi-VN"`, `tts_preset_ref="tts:vi:default"`) -- not inherently wrong, but limits reusability
5. **6-hour max retry delay** means a job with transient failures could block a worker for hours

---

## Recommended Priority

1. **Fix C1 + C2** (TTS client resource management) -- highest production impact
2. **Fix C3** (Dropbox session abort) -- prevents quota leaks
3. **Fix H1** (falsy-value bug) -- correctness bug
4. **Fix H4** (DB connect timeout) -- prevents worker stall
5. **Add conftest.py** (L1) -- reduces test maintenance burden
6. **Address M1** (normalize duplication) -- biggest maintenance risk
