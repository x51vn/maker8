# Editor8 Request Changes Card: Uploader-Ready Output Audit

## 1. Summary

`editor8` hiện đã tạo được `RenderRequest` hợp lệ cho `maker8` render.

Tuy nhiên, output hiện tại vẫn chưa đủ giàu và chưa đủ chặt để `maker8` và downstream uploader có thể xử lý upload production lên:

- YouTube
- TikTok
- Facebook
- và các platform khác

Vấn đề chính không nằm ở phần render.

Vấn đề nằm ở boundary giữa:

- `editor8` output contract
- `maker8` result/manifest
- uploader downstream

Hiện contract vẫn còn:

- thiếu routing data cho publish thật
- thiếu metadata normalized cho uploader
- thừa metadata kiểu free-form không thể dùng trực tiếp
- thiếu test fixtures để khóa semantic “uploader-ready”

## 2. Outputs Reviewed

Review này dựa trên 4 nguồn chính:

- payload thực tế do `editor8` sinh ra ngày 2026-03-20
- golden fixture tối thiểu của `editor8`
- golden fixture đa scene có publish target
- downstream expectation hiện tại trong `xUploader`

## 3. Current State Review

### 3.1 Output thực tế hiện tại

Payload thực tế cho thấy:

- `render_spec.publish.targets` đang rỗng
- `uploader_metadata.tags` chứa các câu factual dài thay vì keyword/tag chuẩn
- `uploader_metadata.hashtags` được sinh trực tiếp từ các câu dài đó
- `uploader_metadata.thumbnail_ref` là raw third-party URL
- `uploader_metadata.credits` chỉ còn `"http"` / `"youtube"`

Kết luận:

- đủ để render
- chưa đủ để upload production

### 3.2 Golden minimal fixture

Golden fixture tối thiểu đang hợp lệ về mặt schema, nhưng:

- không có `uploader_metadata`
- không có publish target
- không thể dùng làm fixture “uploader-ready”

Nó chỉ chứng minh:

- contract parse được
- chưa chứng minh pipeline publish được

### 3.3 Golden multiscene fixture

Golden fixture đa scene có tiến thêm một bước:

- có `publish.targets[0].platform = youtube`
- có `account_ref`
- có `metadata.title`
- có `metadata.description`
- có `params.privacy`

Nhưng vẫn thiếu:

- schedule có timezone rõ ràng
- variant rõ là `video` hay `shorts`
- metadata đủ để build payload cho TikTok / Facebook
- thumbnail ownership rõ ràng
- attribution/licensing chi tiết

### 3.4 Metadata builder hiện tại

`editor8.pipeline.metadata.build_uploader_metadata()` hiện đang:

- lấy `title`
- lấy `description`
- ghép `tags` từ `intent.key_points` và `scene.keywords`
- sinh `hashtags` bằng cách prefix `#` vào `tags`
- suy category từ `intent.goal`
- lấy `thumbnail_ref` bằng URL của asset đầu tiên
- lấy `credits` bằng source kind của asset đầu tiên mỗi scene

Đây là logic quá nhẹ cho production upload.

Nó sinh ra object “trông có vẻ đúng schema” nhưng chưa đủ semantic cho uploader.

### 3.5 Publish target builder hiện tại

`editor8` hiện expand selected channels thành:

- `platform`
- `account_ref`
- `metadata.editor8_channel_id`
- `metadata.channel_name`
- `metadata.channel_description`
- `metadata.channel_id`
- `metadata.channel_url`
- `params = channel.default_publish_config`

Điều này hữu ích cho routing cơ bản, nhưng vẫn chưa đủ để build DTO downstream một cách deterministic.

## 4. What Is Missing

### 4.1 Missing routing intent

Nếu `publish.targets` rỗng thì downstream không biết:

- publish lên đâu
- account nào
- platform nào
- có được autopublish hay chỉ render-only

Muốn go-live production thì phải tách rõ:

- render-only job
- publish-ready job

Không thể coi hai trạng thái này là một.

### 4.2 Missing platform-ready text variants

Một `title` và một `description` chung là không đủ.

Uploader downstream thực tế cần các biến thể theo platform, ví dụ:

- YouTube title / description / tags
- TikTok caption / hashtags
- Facebook post title / summary / link comment

Hiện tại các field này chưa được chuẩn hóa.

### 4.3 Missing publish variant

Downstream cần biết target là:

- long-form video
- short video
- reel
- social post with video

Canvas dọc/ngang không đủ để suy luận an toàn.

Ví dụ:

- video dọc có thể là YouTube Shorts
- cũng có thể là TikTok
- cũng có thể là Facebook Reels

Contract hiện tại chưa nói rõ target variant này.

### 4.4 Missing canonical thumbnail plan

`thumbnail_ref` hiện là raw external URL.

Uploader production lại cần một trong các kiểu rõ ràng sau:

- asset ref nội bộ
- generated thumbnail plan
- final thumbnail artifact ref

Nếu chỉ có URL ngoài thì downstream phải tự đoán:

- có download lại hay không
- có hợp lệ quyền sử dụng hay không
- có đúng aspect ratio / safe crop hay không

### 4.5 Missing attribution and rights detail

`credits: ["http", "youtube"]` gần như vô dụng cho compliance.

Thiếu các thông tin quan trọng:

- asset_ref
- source_url
- provider
- author / channel / creator
- license
- required credit text
- usage restrictions

Nếu go-live mà không có lớp này thì rất khó audit bản quyền.

### 4.6 Missing downstream linking fields

Một số platform flow hiện tại cần thêm field mà contract chưa có chuẩn chung:

- canonical article URL
- CTA link
- summary
- landing page link

Đặc biệt flow Facebook hiện tại cần `summary` và `link`.

### 4.7 Missing publish policy normalization

Các field kiểu:

- `visibility`
- `privacy`
- `scheduled_publish_at`
- `made_for_kids`
- `content_rating`

đang chưa được normalize đủ chặt giữa common layer và per-platform layer.

Nếu không khóa semantics, mỗi uploader sẽ tự hiểu khác nhau.

## 5. What Is Extra Or In The Wrong Place

### 5.1 Sentence-like tags and hashtags

Các câu factual dài hiện đang bị nhét vào:

- `tags`
- `hashtags`

Đây là sai tầng semantic.

`tags` nên là token / keyword / entity / topic ngắn.

`hashtags` nên là social-ready token ngắn, không phải nguyên câu dài.

### 5.2 Generic visibility in common metadata

`uploader_metadata.visibility = "private"` ở common layer chỉ hữu ích một phần.

Trong production, visibility thường là per-platform concern:

- YouTube: private / unlisted / public / scheduled
- TikTok: public / private / followers
- Facebook: page post / reel / visibility rules khác

Nếu giữ common field thì cần định nghĩa rõ:

- nó là default
- hay là canonical global policy

Nếu không, field này chỉ tạo ambiguity.

### 5.3 Raw source URL as thumbnail reference

`thumbnail_ref` đang trỏ thẳng tới URL ngoài.

Đây không phải “ref” ổn định theo nghĩa production pipeline.

Nó là source URL.

Nên tách rõ:

- `thumbnail_source_url`
- `thumbnail_asset_ref`
- `thumbnail_output_ref`

### 5.4 Channel description in publish target metadata

`channel_description` có thể hữu ích cho UI/debug nhưng không phải field canonical cho uploader runtime.

Nó nên là optional/debug info, không nên bị hiểu là field cần thiết cho contract upload.

## 6. Downstream Reality Check

`xUploader` hiện không consume trực tiếp `RenderRequest`.

Nó đang cần một upload DTO mang tính thao tác runtime hơn, ví dụ:

### 6.1 YouTube flow

Hiện downstream YouTube flow cần tối thiểu:

- `channel_name`
- `title`
- `description` hoặc `video_description`
- `hash_tags`
- `category`
- `video_file_path` và/hoặc `shorts_file_path`
- `thumbnail_file_path`

### 6.2 Facebook flow

Hiện downstream Facebook flow cần tối thiểu:

- `profile`
- `fb_url`
- `title`
- `summary`
- `link`

### 6.3 Implication

`editor8` không nên biết local file path sau render.

Nhưng `editor8` phải phát ra đủ semantic metadata để một adapter ở phía `maker8` / uploader result consumer có thể build upload DTO mà không phải query ngược về DB hay tự đoán.

## 7. Request Changes

### 7.1 Strengthen `uploader_metadata`

Giữ `uploader_metadata` là common layer, nhưng cần mở rộng tối thiểu:

- `title`
- `short_title`
- `description`
- `summary`
- `lang`
- `keywords`
- `hashtags`
- `category`
- `content_rating`
- `canonical_url`
- `cta_url`
- `thumbnail_asset_ref`
- `thumbnail_source_url`
- `thumbnail_strategy`
- `source_attributions[]`

Đề xuất shape cho attribution:

```json
{
  "asset_ref": "yt_vQ8PCcawXzA",
  "provider": "youtube",
  "source_url": "https://www.youtube.com/watch?v=vQ8PCcawXzA",
  "creator": "Channel Name",
  "license": "standard_youtube_license",
  "credit_text": "Source: Channel Name / YouTube",
  "usage_restrictions": []
}
```

### 7.2 Strengthen `publish.targets[]`

`publish.targets[]` không chỉ là routing list.

Mỗi target phải đủ để downstream adapter build publish intent rõ ràng.

Đề xuất mỗi target tối thiểu có:

- `platform`
- `account_ref`
- `variant`
- `enabled`
- `metadata`
- `params`

Trong đó:

- `variant` ví dụ: `long_video`, `short_video`, `reel`, `social_post`
- `metadata` là per-platform text override
- `params` là execution/policy config

### 7.3 Define platform-specific minimums

#### YouTube

Per-target metadata hoặc params phải đủ để build:

- `channel_name`
- `title`
- `description`
- `hash_tags`
- `category`
- `visibility`
- `scheduled_publish_at`
- `variant`
- `thumbnail_policy`

#### TikTok

Per-target metadata hoặc params phải đủ để build:

- `caption`
- `hashtags`
- `visibility`
- `scheduled_publish_at`
- `variant`

#### Facebook

Per-target metadata hoặc params phải đủ để build:

- `profile`
- `fb_url`
- `title`
- `summary`
- `link`
- `variant`

### 7.4 Normalize tags and hashtags

`editor8` phải normalize:

- không cho sentence-length tags
- bỏ dấu câu không cần thiết
- dedupe case-insensitive
- giới hạn độ dài từng tag/hashtag
- giữ hashtags là social token thật

### 7.5 Make publish-readiness explicit

Nếu request không có target publish hợp lệ thì phải thể hiện rõ là:

- render-only
- draft-only
- not upload-ready

Không nên để downstream suy luận từ `publish.targets=[]`.

### 7.6 Make thumbnail ownership explicit

Không dùng raw URL ngoài như thumbnail canonical ref.

Phải tách rõ:

- source candidate
- selected thumbnail asset
- final thumbnail artifact

### 7.7 Add uploader-ready fixtures

Phải thêm golden fixtures riêng cho:

- render-only job
- YouTube long-form job
- YouTube Shorts job
- TikTok short job
- Facebook video/post job

Các fixture này phải có:

- `uploader_metadata`
- non-empty `publish.targets`
- valid routing fields
- valid thumbnail strategy
- valid attribution objects

## 8. Suggested Contract Example

Ví dụ contract tối thiểu đủ dùng hơn:

```json
{
  "uploader_metadata": {
    "title": "Khủng hoảng eo biển Hormuz",
    "short_title": "Khủng hoảng Hormuz",
    "description": "Phân tích lời kêu gọi hộ tống và hệ quả toàn cầu.",
    "summary": "Tình hình leo thang tại eo biển Hormuz và phản ứng quốc tế.",
    "lang": "vi-VN",
    "keywords": ["hormuz", "donald trump", "oil tanker", "iran"],
    "hashtags": ["#Hormuz", "#OilMarket", "#Trump"],
    "category": "news",
    "content_rating": "general",
    "canonical_url": "https://example.com/articles/hormuz-crisis",
    "cta_url": "https://example.com/articles/hormuz-crisis",
    "thumbnail_asset_ref": "icrawl_d98a02dacf51",
    "thumbnail_source_url": "https://www.advocate.com/media-library/truth-social-post-donald-trump.jpg",
    "thumbnail_strategy": "source_asset",
    "source_attributions": []
  },
  "render_spec": {
    "publish": {
      "targets": [
        {
          "platform": "youtube",
          "account_ref": "yt:tin-tuc-5-1",
          "variant": "short_video",
          "enabled": true,
          "metadata": {
            "channel_name": "Tin Tức 5.1",
            "title": "Khủng hoảng Hormuz: Hệ quả toàn cầu",
            "description": "Phân tích ngắn về rủi ro năng lượng, quân sự và thương mại.",
            "hash_tags": ["#Hormuz", "#OilMarket", "#MiddleEast"]
          },
          "params": {
            "visibility": "private",
            "scheduled_publish_at": null
          }
        }
      ]
    }
  }
}
```

## 9. Acceptance Criteria

- Một `RenderRequest` publish-ready phải đủ dữ liệu để downstream build upload DTO mà không cần query ngược về `editor8`.
- `publish.targets=[]` phải được hiểu rõ là render-only và không được coi là uploader-ready.
- `tags` và `hashtags` phải là token ngắn đã normalize, không phải câu dài.
- `thumbnail_ref` không được chỉ là raw third-party URL.
- `credits` phải được thay bằng attribution object có thể audit.
- Cần có fixture và test riêng cho YouTube, TikTok, Facebook.

## 10. Definition Of Done

- contract `render_contracts/render_spec.py` được cập nhật
- `editor8.pipeline.metadata` được nâng cấp
- `editor8` publish target builder được chuẩn hóa theo platform
- fixtures uploader-ready được thêm vào test suite
- `maker8` manifest / result tiếp tục pass-through đầy đủ
- downstream adapter có thể build upload DTO cho ít nhất:
  - YouTube
  - TikTok
  - Facebook

