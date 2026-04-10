# Maker8 / Editor8 Media Quality Requests 2026-04-07

## Purpose

Tài liệu này gom 5 yêu cầu mới:

1. bỏ âm thanh gốc khỏi video source trong `maker8`
2. giới hạn video YouTube được chọn dưới 10 phút
3. cho `editor8` khả năng tự động cập nhật `yt-dlp`
4. nâng cấp `icrawler` để cải thiện tìm kiếm ảnh Google
5. nâng cấp agent analyzer của `editor8` để hiểu article tốt hơn và tìm ảnh/video/text tốt hơn

## Scope And Investigation Limits

- Phần `maker8` đã được đối chiếu trực tiếp với code trong workspace hiện tại.
- Phần `editor8` không có source code trong workspace này, nên các mục liên quan `editor8` là investigation ở mức boundary/runtime requirement, dựa trên:
  - contract chung `render_contracts`
  - docs cross-repo còn lại trong repo này
  - design pattern đã có trong `maker8`
  - tài liệu chính thức của `yt-dlp` và `icrawler`

## Executive Summary

- Yêu cầu số 1 là bug thật ở `maker8`: video layer đang giữ audio gốc.
- Yêu cầu số 2 đã có một phần support ở `maker8`: `max_duration_sec` đang được connector YouTube đọc và enforce, nhưng chưa thấy bằng chứng `editor8` search side luôn set và prefilter ngắn hơn 10 phút.
- Yêu cầu số 3 thực tế đã có implementation baseline ở `maker8`: startup validation + background updater + managed binary path. `editor8` nên mirror pattern này thay vì tự nghĩ một cơ chế khác.
- Yêu cầu số 4 cần làm ở `editor8`; hiện workspace này không chứa dependency `icrawler`, nhưng upstream package hiện đã có bản mới `0.6.10` trên PyPI ngày 2025-02-11. Chỉ bump version là chưa đủ vì GoogleImageCrawler vốn có lịch sử break theo DOM/anti-bot.
- Yêu cầu số 5 là yêu cầu product/quality lớn nhất. Không thấy code `editor8` trong workspace này, nên phần này nên được triển khai như một analyzer pipeline mới có output contract rõ ràng, thay vì chỉ “prompt tốt hơn”.

---

## 1. Remove Source Video Audio In Maker8

## Investigation

`maker8` hiện mở video layer bằng `VideoFileClip(...)` và không strip audio:

- `src/maker8/rendering/layers.py:46`

Video layer sau đó được đưa thẳng vào `CompositeVideoClip`:

- `src/maker8/rendering/composer.py:608-638`

`composer` chỉ set audio tổng khi có `tts` hoặc `audio_tracks`:

- `src/maker8/rendering/composer.py:640-655`

Ngoài ra, một số effect plugin còn chủ động preserve audio của clip nguồn:

- `src/maker8/plugins/effects/color_overlay.py:63-64`
- `src/maker8/plugins/effects/blur.py:75-76`
- `src/maker8/plugins/effects/grayscale.py:73-74`
- `src/maker8/plugins/effects/zoom_pan.py:122-123`

Điều này phù hợp với triệu chứng bạn thấy: video output vẫn giữ tiếng gốc từ asset video.

## Requirement

`maker8` phải coi audio gốc của `video` layer là **muted by default**.

Audio trong output chỉ được đến từ:

- TTS narration
- `scene.audio_tracks`
- một cơ chế explicit future flag nếu product thật sự muốn “use source video audio”

## Required Changes

- Sửa `src/maker8/rendering/layers.py` để strip audio ngay khi build video layer.
- Review toàn bộ effect plugins nào đang copy `source_clip.audio`; chúng phải không vô tình re-attach source audio sau khi layer đã bị mute.
- Nếu sau này cần preserve source audio, phải là opt-in rõ ràng ở contract chứ không phải mặc định ngầm.

## Acceptance Criteria

- Scene có video background + narration không còn nghe tiếng gốc từ asset video.
- Scene có `audio_tracks` vẫn phát audio track đúng như contract.
- Có regression test chứng minh:
  - video layer mặc định không có audio
  - audio chỉ xuất hiện khi đến từ `tts` hoặc `audio_tracks`

---

## 2. Limit YouTube Videos To Under 10 Minutes

## Investigation

`render_contracts` đã có field `AssetSourceOptions.max_duration_sec`.

Ở runtime hiện tại, YouTube connector của `maker8` thực sự đã đọc và enforce field này:

- đọc `max_duration_sec`: `src/maker8/plugins/sources/youtube.py:279-281`
- log giá trị: `src/maker8/plugins/sources/youtube.py:297-305`
- reject video quá dài: `src/maker8/plugins/sources/youtube.py:348-350`

Nhưng docs nội bộ hiện vẫn bị drift:

- `docs/MAKER8_SOURCE_OF_TRUTH.md:261` vẫn ghi `max_duration_sec` là `RESERVED`

Nói cách khác:

- `maker8` đã có guardrail downstream
- nhưng yêu cầu của bạn là **giới hạn từ khâu tìm kiếm/chọn candidate**, tức là chủ yếu nằm ở `editor8`

## Requirement

`editor8` phải chỉ tìm/chọn YouTube candidate có duration `< 600s`.

`maker8` vẫn phải giữ check downstream như safety net.

## Required Changes

### Editor8

- Search/retrieval layer của `editor8` phải prefilter video candidate theo duration `< 600s`.
- Khi materialize asset YouTube vào `RenderRequest`, `editor8` phải set:

```json
"options": {
  "max_duration_sec": 600
}
```

- Nếu search provider chưa trả duration trực tiếp, `editor8` phải bổ sung một metadata probe step trước khi chọn asset cuối cùng.

### Maker8

- Giữ enforcement hiện tại ở `youtube.resolve()`.
- Cập nhật docs nội bộ vì hiện docs đang mô tả sai runtime.
- Cân nhắc thêm một default cap ở runtime nếu upstream không set `max_duration_sec`, nhưng đây là product decision; không nên tự ý silently change behavior nếu chưa chốt.

## Acceptance Criteria

- `editor8` không còn submit YouTube assets > 600 giây trong flow bình thường.
- `maker8` reject rõ ràng nếu upstream vẫn gửi video > 600 giây.
- Docs/source-of-truth không còn ghi `max_duration_sec` là `RESERVED`.

---

## 3. Editor8 Must Also Auto-Update yt-dlp

## Investigation

`maker8` hiện đã có baseline implementation cho `yt-dlp` managed runtime:

- startup validation:
  - `src/maker8/app.py:77-87`
- updater wiring:
  - `src/maker8/app.py:149-160`
- config:
  - `src/maker8/config.py:91-107`
- updater service:
  - `src/maker8/services/ytdlp_updater.py:1-252`

Thiết kế hiện tại của `maker8` là:

- dùng managed binary path
- check release channel `stable`/`nightly`
- download từ GitHub releases
- verify checksum
- activate bằng symlink `current`
- skip activation khi worker đang bận

Đây là baseline tốt để `editor8` reuse.

Ngoài ra, tài liệu chính thức `yt-dlp` cũng support nhiều channel và khuyến nghị update khi `stable` lỗi, thậm chí dùng `nightly` nếu đang gặp YouTube issue:

- `yt-dlp` README / update docs: https://github.com/yt-dlp/yt-dlp
- installation wiki: https://github.com/yt-dlp/yt-dlp/wiki/Installation

## Requirement

`editor8` phải có cơ chế auto-update `yt-dlp` tương tự `maker8` cho tất cả use case:

- YouTube search
- YouTube metadata probe
- YouTube candidate validation

## Required Changes

- Không rely vào `pip install -U yt-dlp` giữa runtime.
- `editor8` phải quản lý `yt-dlp` như managed binary riêng, giống pattern của `maker8`.
- Nếu có thể, trích `YtdlpUpdater` thành shared package/module dùng chung giữa `editor8` và `maker8` để tránh drift.
- Nếu chưa thể shared ngay, ít nhất `editor8` phải mirror các nguyên tắc sau:
  - channel pinning: `stable`/`nightly`
  - checksum verification
  - atomic activation
  - rollback-aware status
  - log current version + last update status

## Acceptance Criteria

- `editor8` log được active `yt-dlp` path + version khi startup.
- `editor8` có thể check và download bản mới theo lịch.
- Update không diễn ra theo kiểu in-place overwrite một executable đang được dùng.
- Có state/metric cho:
  - current version
  - previous version
  - last check
  - last success
  - last failure

---

## 4. Upgrade icrawler For Google Image Search

## Investigation

Workspace hiện tại không chứa source/dependency list của `editor8`, nên tôi không xác minh được version `icrawler` mà `editor8` đang dùng.

Tuy vậy, upstream package hiện có bản mới trên PyPI:

- `icrawler 0.6.10` published `2025-02-11`
- PyPI: https://pypi.org/project/icrawler/

Official docs của `icrawler` vẫn mô tả `GoogleImageCrawler` là built-in crawler.

Tuy nhiên, upstream issue tracker cũng cho thấy GoogleImageCrawler có thể break khi Google đổi DOM/anti-bot behavior:

- issue example: https://github.com/hellock/icrawler/issues/125

Vì vậy:

- upgrade là hợp lý
- nhưng chỉ upgrade version **không đủ** để “đảm bảo” Google image search luôn ổn

## Requirement

`editor8` phải nâng `icrawler` lên version mới nhất đang được team chấp nhận, đề xuất hiện tại là `0.6.10`, và đồng thời harden image search path.

## Required Changes

- Bump `icrawler` lên `0.6.10`.
- Thêm smoke test/integration test cho Google image search path.
- Thêm graceful fallback nếu Google crawler lỗi:
  - fallback provider khác
  - retry chiến lược riêng
  - clear error classification để operator biết Google parser đang hỏng
- Nếu `editor8` đang rely vào parser internals của `icrawler`, review compatibility trước khi bump.

## Acceptance Criteria

- Dependency được bump lên `icrawler==0.6.10` hoặc pin tương đương đã được chấp thuận.
- Có test chứng minh Google image search path còn chạy.
- Nếu Google crawler hỏng, `editor8` degrade/fallback rõ ràng thay vì im lặng fail.

---

## 5. Upgrade Editor8 Agent Analyzer For Better Article Understanding

## Investigation

Không có source code `editor8` trong workspace hiện tại, nên tôi không thể review implementation analyzer hiện tại.

Tuy nhiên, yêu cầu của bạn rất rõ về output mong muốn:

- hiểu article đầu vào tốt hơn
- từ đó tìm ảnh tốt hơn
- tìm video tốt hơn
- tìm text tốt hơn

Đây không nên được xử lý như một thay đổi prompt nhỏ.
Nó nên được xem là một analyzer pipeline có output contract rõ ràng cho downstream retrieval.

## Requirement

`editor8` cần một analyzer agent/analyzer pipeline mới, có khả năng biến article input thành structured search plan giàu context.

## Required Output Contract For Analyzer

Analyzer nên sinh ra ít nhất các nhóm field sau:

- topic chính
- subtopics
- people / organizations / places
- timeframe / recency / historical period
- locale / country / language
- article tone:
  - breaking news
  - explainer
  - human story
  - finance / politics / science / entertainment
- visual entities:
  - objects
  - scenes
  - landmarks
  - activities
  - symbolic imagery
- media constraints:
  - real footage vs illustrative footage
  - portrait vs landscape preference
  - short-form suitability
  - safe / unsafe imagery
- search queries by modality:
  - image queries
  - video queries
  - supporting text/caption keywords
- confidence / ambiguity notes:
  - unknown entities
  - disputed timeframe
  - weakly grounded visual suggestions

## Required Changes In Editor8

- Tách analyzer output thành structured schema, không chỉ free-form prose.
- Cho analyzer đọc article theo chunk + summary + entity pass nếu input dài.
- Analyzer phải sinh:
  - primary query set
  - fallback query set
  - “do not search” / “avoid” constraints
- Retrieval layer phải dùng analyzer output để query khác nhau cho:
  - images
  - videos
  - supporting text snippets
- Cần offline evaluation set với article thực tế để đo chất lượng search result trước/sau.

## Suggested Quality Metrics

- image relevance
- video relevance
- text relevance
- duplicate rate
- hallucinated entity rate
- irrelevant celebrity/stock-photo rate
- % query sets requiring manual correction

## Acceptance Criteria

- Với cùng một article, analyzer mới sinh query cụ thể và grounded hơn analyzer cũ.
- Kết quả media search giảm rõ stock-photo generic và video không liên quan.
- Analyzer output có schema ổn định, có thể test được.

---

## Cross-Repo Priorities

## P0

- Fix `maker8` để strip source video audio mặc định.
- Enforce policy video YouTube `< 10 phút` ở `editor8` search layer.
- `editor8` mirror `yt-dlp` auto-update capability của `maker8`.

## P1

- Upgrade `icrawler` + thêm smoke/integration coverage cho Google image search.
- Update source-of-truth/docs về `max_duration_sec`.

## P2

- Rework `editor8` agent analyzer thành structured media-planning pipeline.

---

## Notes

- Tài liệu này là investigation + requirement card, không phải implementation.
- Phần `maker8` đã có đủ bằng chứng từ code local.
- Phần `editor8` là cross-repo requirement vì source `editor8` không có trong workspace hiện tại.
- Sources used for package/runtime investigation:
  - `yt-dlp` official repo and installation/update docs:
    - https://github.com/yt-dlp/yt-dlp
    - https://github.com/yt-dlp/yt-dlp/wiki/Installation
  - `icrawler` official package page:
    - https://pypi.org/project/icrawler/
  - `icrawler` issue evidence for Google breakage:
    - https://github.com/hellock/icrawler/issues/125
