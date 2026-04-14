# uploader8 Rename, Review, And Integration Requirements

Date: 2026-04-10  
Status: Investigation complete, implementation not started

## 1. Mục tiêu tài liệu

Tài liệu này chốt 3 mục tiêu:

1. mô tả rõ yêu cầu đổi tên `xUploader` thành `uploader8`, bao gồm cả folder name và project name
2. review trạng thái hiện tại của `xUploader` dựa trên codebase đã verify
3. liệt kê toàn bộ needed changes để đạt luồng ổn định, nhất quán và production-oriented:

`editor8 -> maker8 -> uploader8 -> YouTube, Facebook`

Tài liệu này dùng một số nguyên tắc consistency bắt buộc:

- `account_ref` là identity chuẩn cho publish target
- `publish.targets[]` là contract chuẩn từ `editor8`
- `video.render.result.v1` là handoff chuẩn từ `maker8`
- `video.publish.result.v1` là publish outcome chuẩn của `uploader8`
- `uploader_metadata.channel_id` là field legacy, chỉ giữ tạm thời để tương thích ngược

## 2. Bối cảnh dự án hiện tại

### 2.1 Trạng thái ở `editor8`

Qua code hiện tại:

- `editor8` build `publish.targets[]` tại `backend/src/editor8/api/routes.py`
- mỗi target có `platform`, `account_ref`, `variant`, `metadata`, `params`
- `account_ref` được derive từ `provider_config.channel_id` hoặc `provider_config.channel_url`
- `editor8` assembler tại `backend/src/editor8/pipeline/assembler.py` tạo `publish_intent="publish_ready"` khi có target hợp lệ
- `editor8` cũng merge metadata chung từ `uploader_metadata` vào từng platform target

Kết luận:

- `editor8` đã đi theo hướng contract-based publishing
- `editor8` không còn ở mô hình “gửi message ad-hoc riêng cho từng uploader”

### 2.2 Trạng thái ở `maker8`

Qua code hiện tại:

- `maker8` consume `video.render.request.v1`
- `maker8` emit `video.render.result.v1`
- `maker8` result hiện đã carry:
  - `dropbox`
  - `uploader_metadata`
  - `publish_targets`
  - `trace`
- `maker8` docs cũng mô tả publisher worker là component downstream/future

Kết luận:

- `maker8` đã là renderer/handoff worker
- uploader đúng kiến trúc phải consume `RenderResult`, không phải consume payload tùy biến riêng

### 2.3 Trạng thái ở `xUploader`

Qua code hiện tại:

- `xUploader` không consume Kafka; nó consume RabbitMQ trong `main.py`
- `xUploader` không consume `RenderResult`; nó consume message legacy có các field như:
  - `publishing_channel`
  - `dbx_article_dir`
  - `article_dir`
  - `channel_name`
  - `video_file_path`
  - `shorts_file_path`
  - `hash_tags`
  - `profile`
  - `fb_url`
- `xUploader` còn download cả folder từ Dropbox rồi tự rewrite path local

Kết luận:

- `xUploader` hiện là legacy publisher worker cũ
- rename đơn thuần là chưa đủ
- cần thay đổi kiến trúc và contract để nó có thể trở thành `uploader8`

## 3. Kết luận điều tra tổng quan

Sau khi review end-to-end, kết luận chính là:

1. `xUploader` hiện không tương thích trực tiếp với flow chuẩn của `editor8` và `maker8`
2. vấn đề cốt lõi không nằm ở tên project, mà nằm ở transport mismatch, contract mismatch, artifact handoff mismatch, identity mismatch và cleanup legacy chưa hoàn thành
3. `xUploader` hiện chưa phải một publisher worker “solid but simple”; nó là tập hợp logic legacy bám vào RabbitMQ, Dropbox folder layout và Selenium profile local
4. muốn đạt flow đúng nghĩa `editor8 -> maker8 -> uploader8 -> YouTube/Facebook`, phải chuyển `xUploader` sang contract và boundary mới, sau đó mới rename và cut over

## 4. Yêu cầu rename `xUploader` thành `uploader8`

### 4.1 Phạm vi rename bắt buộc

Các thành phần sau phải được rename đồng bộ:

- repo/folder:
  - từ `/home/<user>/IdeaProjects/xUploader`
  - thành `/home/<user>/IdeaProjects/uploader8`
- project name trong `README`, docs, logs, service descriptions
- tên service/timer:
  - `rb_xuploader.service`
  - `rb_xuploader.timer`
- mọi hard-coded path đang trỏ tới `xUploader`
- mọi tài liệu, script bootstrap, runbook, deploy notes, shell launcher

### 4.2 Nguyên tắc rename

- rename phải là atomic rename ở tầng naming, không tạo song song `xUploader` và `uploader8` lâu dài
- không giữ tên cũ trong README, systemd unit, log file naming, docs title
- nếu vẫn cần compatibility trong rollout, chỉ cho phép alias trong deployment layer ngắn hạn, không cho phép dual naming trong code source

### 4.3 Kết luận về rename

Rename là required, nhưng rename chỉ được coi là hoàn thành khi:

- folder name đã đổi
- project name đã đổi
- runtime service names đã đổi
- documentation đã đổi
- contract publish mới đã được chốt, để `uploader8` không còn là “xUploader cũ với tên mới”

## 5. Review `xUploader` hiện tại

### 5.1 Findings mức nghiêm trọng cao

#### A. Transport mismatch

`xUploader/main.py` đang là RabbitMQ consumer (`pika`) với queue/exchange `publishing_queue`.

Impact:

- không ăn khớp với flow hiện tại của `editor8` và `maker8`, vốn dùng Kafka
- không thể trở thành publisher worker chuẩn của `maker8` nếu vẫn giữ RabbitMQ branch riêng

Requirement:

- bỏ RabbitMQ khỏi flow chính của publisher worker
- `uploader8` phải consume Kafka handoff từ `maker8`

#### B. Contract mismatch

`editor8` và `maker8` đang đi theo contract `publish.targets[]` với `platform + account_ref + metadata + params`, trong khi `xUploader` vẫn chờ payload legacy như:

- YouTube:
  - `channel_name`
  - `video_file_path`
  - `shorts_file_path`
  - `article_dir`
  - `hash_tags`
- Facebook:
  - `profile`
  - `fb_url`
  - `title`
  - `summary`
  - `link`

Impact:

- interface drift rất lớn
- cùng một khái niệm publish target nhưng có 2 contract khác nhau
- không thể đảm bảo consistency xuyên `editor8`, `maker8`, `uploader8`

Requirement:

- `uploader8` phải consume contract chuẩn từ `maker8`
- mọi adapter YouTube/Facebook phải nhận input từ `RenderResult + PublishTarget`, không được đọc DTO legacy riêng

#### C. Artifact handoff mismatch

`xUploader` đang phụ thuộc `dbx_article_dir` và `article_dir`, download nguyên folder từ Dropbox, rồi sau upload YouTube thì xóa cả local dir và Dropbox folder.

Impact:

- không khớp với `maker8`, nơi artifact chính là `dropbox.video` và `dropbox.manifest`
- cleanup hiện tại nguy hiểm nếu có nhiều publish targets
- không còn auditability và retry safety sau khi platform đầu tiên publish xong

Requirement:

- `uploader8` chỉ nên consume artifact refs từ `RenderResult`
- cleanup artifact phải có retention policy rõ ràng
- tuyệt đối không xóa nguồn artifact ngay sau một target thành công

#### D. Identity mismatch

`editor8` và `maker8` đang đi theo `account_ref`, còn `xUploader` YouTube vẫn switch account bằng `channel_name`, Facebook vẫn chọn page bằng `profile`.

Impact:

- identity không ổn định
- rất dễ drift khi đổi tên channel/page hiển thị
- không thể dùng `account_ref` làm source of truth xuyên hệ thống

Requirement:

- `account_ref` phải là identity chuẩn xuyên `editor8`, `maker8`, `uploader8`
- `channel_name` hoặc `profile` chỉ còn là display metadata, không phải runtime identity

#### E. Một message chỉ xử lý một platform

`xUploader/main.py` dùng `publishing_channel` để quyết định `YOUTUBE` hoặc `FACEBOOK`.

Impact:

- không tương thích với `publish.targets[]`
- một render result có nhiều targets sẽ không có execution model đúng

Requirement:

- `uploader8` phải iterate `publish_targets[]`
- cùng một `job_id` phải có thể publish nhiều targets và tạo `publish_report[]`

### 5.2 Findings mức nghiêm trọng trung bình

#### A. Runtime phụ thuộc workstation local

`youtuber/uploader.py` và `facebooker/uploader.py` hard-code:

- `USER_DATA_DIR = '/home/<user>/Downloads/ProfileX'`
- `PROFILE_DIR = '/home/<user>/Downloads/ProfileX'`

Impact:

- không portable
- không scale được qua nhiều host/container
- không tách được account config khỏi machine state

Requirement:

- account execution profile phải được resolve qua config/account registry
- không hard-code profile path local trong code

#### B. Config monolithic và legacy

`config.py` nhồi chung:

- YouTube
- Chrome
- RabbitMQ
- Facebook
- Database
- Redis
- Dropbox

Impact:

- khó biết phần nào thực sự dùng
- tăng code drift và dead config

Requirement:

- tách config theo boundary:
  - app/worker
  - platform adapters
  - artifact store
  - observability

#### C. README sai thực tế

`Readme.md` hiện chỉ ghi:

```shell
uvicorn main:app --host 0.0.0.0 --port 3002
```

Trong khi `main.py` không có `app` FastAPI.

Impact:

- onboarding sai
- tài liệu không đáng tin cậy

Requirement:

- viết lại README theo runtime thực tế của worker
- nếu không có HTTP API thì bỏ `uvicorn`, `fastapi`, `starlette`

#### D. Gần như không có automated tests

Các file `test.py`, `tests/test.py`, `tests/clean_dbox.py` là manual scripts, không phải test suite production-grade.

Impact:

- không có regression safety
- không verify được contract publish mới

Requirement:

- phải có automated tests cho contract mapping, target routing, artifact handling, publish result building

### 5.3 Findings về code thừa, code unused, dead code, redundant code

Các dấu hiệu rõ nhất:

- `Readme.md` mô tả `FastAPI/uvicorn` nhưng runtime thực tế không dùng
- `requirements.txt` chứa `fastapi`, `uvicorn`, `starlette`, `psycopg`, `redis`, `SQLAlchemy` nhưng current worker path không chứng minh được chúng là dependency cốt lõi
- `test.py` root import `from uploader import YoutubeUploader`, trong khi module đó không tồn tại ở top-level hiện tại
- `PLAN.MD` và `PLAN.MD.bak` là tài liệu refactor dang dở, không phản ánh state code thực tế
- `YOUTUBE_UPLOADER_TASKS.md` và `youtuber/REFACTORING_UPLOADER.md` cho thấy hướng refactor cũ nhưng chưa trở thành kiến trúc thật
- `.env`, `auth_result.pickle`, `dbx8/auth_result.pickle`, `venv/`, `logs/`, `__pycache__/` không nên là phần source tracked của project production
- repo đang dirty với `login.py` modified và nhiều file `dbx8/*` untracked

Kết luận:

- `xUploader` hiện chứa nhiều legacy residue
- nếu không cleanup trước hoặc song song với refactor, drift sẽ tiếp tục tăng

### 5.4 Nhận xét về cấu trúc thư mục hiện tại

Cấu trúc hiện tại:

- root chứa quá nhiều runtime concern, docs tạm, logs, manual scripts, credentials artifacts
- package naming không phản ánh boundary nghiệp vụ rõ ràng
- `dbx8`, `youtuber`, `facebooker` đang là các khối kỹ thuật ghép cạnh nhau, thiếu application layer chính danh

Đánh giá:

- chưa solid
- chưa simple
- khó maintain

Vấn đề chính:

- thiếu rõ ràng giữa transport layer, artifact layer, platform adapter layer, account registry layer và publish result layer
- repo root đang bị dùng như working directory hơn là source tree production

## 6. Trạng thái đích cho `uploader8`

`uploader8` phải là publisher worker downstream của `maker8`, không phải uploader script độc lập.

### 6.1 Responsibility chuẩn của `uploader8`

- consume `video.render.result.v1`
- validate publish handoff
- resolve artifact video/thumbnail/manifest từ refs đã được `maker8` emit
- iterate `publish_targets[]`
- dispatch đến adapter theo `platform`
- publish lên YouTube/Facebook
- build `video.publish.result.v1`
- hỗ trợ retry, idempotency, audit log, partial failure

### 6.2 Input chuẩn

`uploader8` phải lấy input từ:

- `job_id`
- `job_key`
- `dropbox.video`
- `dropbox.manifest`
- `uploader_metadata`
- `publish_targets`
- `trace`

Không được lấy input từ payload legacy kiểu:

- `publishing_channel`
- `dbx_article_dir`
- `article_dir`
- `channel_name`
- `profile`

### 6.3 Identity chuẩn

Identity chuẩn phải là:

- `PublishTarget.account_ref`

Metadata hiển thị có thể giữ:

- `channel_name`
- `channel_id`
- `channel_url`

Nhưng các field này chỉ là descriptive metadata, không phải primary runtime identity.

## 7. Needed changes để `uploader8` hoạt động hoàn hảo cùng `editor8` và `maker8`

### 7.1 Contract changes

#### Required

- `uploader8` phải consume `RenderResult` của `maker8`
- `uploader8` phải map mỗi `PublishTarget` thành một publish attempt riêng
- `account_ref` phải là routing key chuẩn cho account/channel/page
- `uploader_metadata.channel_id` chỉ còn là compatibility field; không dùng làm source of truth

#### Required mapping rules

- YouTube adapter đọc:
  - `target.platform == "youtube"`
  - `target.account_ref`
  - `target.metadata.title` hoặc fallback `uploader_metadata.title`
  - `target.metadata.description` hoặc fallback `uploader_metadata.description`
  - `target.metadata.hash_tags` hoặc fallback `uploader_metadata.hashtags`
  - `target.metadata.category` hoặc fallback `uploader_metadata.category`
  - `target.metadata.visibility` hoặc fallback `uploader_metadata.visibility`
- Facebook adapter đọc:
  - `target.platform == "facebook"`
  - `target.account_ref`
  - `target.metadata.title` hoặc fallback `uploader_metadata.title`
  - `target.metadata.summary` hoặc fallback `uploader_metadata.description`
  - `target.metadata.link` hoặc fallback `uploader_metadata.canonical_url`

#### Required behavior

- một `job_id` có thể publish nhiều targets
- mỗi target phải có result riêng trong `publish_report[]`
- failure của một target không được làm mất result của target khác

### 7.2 Transport changes

#### Required

- bỏ RabbitMQ khỏi publisher main path
- consume Kafka topic `video.render.result.v1`
- emit Kafka topic `video.publish.result.v1`
- có DLQ hoặc retry strategy rõ ràng cho publish stage

#### Not allowed

- không duy trì 2 publisher flows song song lâu dài: một flow RabbitMQ legacy và một flow Kafka chuẩn

### 7.3 Artifact handling changes

#### Short-term target state

Để giảm scope và tận dụng `maker8` hiện tại:

- giữ Dropbox như artifact store handoff ngắn hạn
- nhưng `uploader8` chỉ được dùng `dropbox.video` và `dropbox.manifest` từ `RenderResult`
- không còn phụ thuộc `article_dir` hoặc `dbx_article_dir`

#### Required

- tải đúng artifact file được `maker8` emit
- không download cả folder nếu không cần
- không rewrite payload về local DTO legacy
- không xóa Dropbox artifact ngay sau target đầu tiên thành công
- cleanup phải theo retention policy hoặc background janitor riêng

### 7.4 Account and credential changes

#### Required

- `editor8` là source of truth cho channel/account selection
- `uploader8` phải resolve execution config theo `account_ref`
- không hard-code channel/page name để chọn runtime account
- không hard-code browser profile path trong source code

#### Recommended execution model

- `editor8` quản lý account/channel metadata và keys/secrets
- `uploader8` đọc execution config qua một account registry keyed by `account_ref`
- phase hiện tại có thể dùng plain text secrets nếu project policy đã chấp nhận, nhưng boundary ownership vẫn phải rõ ràng

### 7.5 YouTube adapter changes

#### Required

- bỏ dependency vào `channel_name` như input bắt buộc
- chuyển sang `account_ref` làm selector chính
- tách browser automation khỏi message transport
- normalize input metadata ngay trước khi publish, không normalize rải rác trong consumer
- trả về result structured cho từng publish attempt

#### Required cleanup

- bỏ assumption luôn có `video_file_path` hoặc `shorts_file_path` từ legacy payload
- dùng artifact ref của `maker8`
- review lại logic upload cả horizontal và shorts; target state cần rule rõ ràng:
  - upload 1 artifact chính theo publish target
  - nếu cần multi-variant thì phải được mô tả bởi contract, không hard-code ngầm

### 7.6 Facebook adapter changes

#### Required

- bỏ dependency vào `profile` và `fb_url` như input gốc từ upstream legacy payload
- map page/account từ `account_ref`
- chỉ dùng `metadata` và `params` chuẩn từ `PublishTarget`
- tách “post body” và “comment with link” thành behavior có config rõ ràng, không hard-code ngầm

#### Required review

- xác định rõ post model mong muốn:
  - post title only
  - post title + summary
  - post title + comment chứa link
- model này phải được document trong contract và áp dụng nhất quán giữa `editor8` và `uploader8`

### 7.7 Publish result and observability changes

#### Required

- emit `video.publish.result.v1`
- report theo từng target:
  - `platform`
  - `account_ref`
  - `status`
  - `post_id` hoặc equivalent identifier
  - `url` nếu có
  - `error` nếu có
- giữ `trace.correlation_id`
- log structured theo `job_id`, `job_key`, `platform`, `account_ref`

#### Required safety

- idempotency theo `job_id + target.account_ref + platform`
- retry policy rõ ràng
- phân biệt lỗi retryable và non-retryable

### 7.8 Testing changes

#### Required test layers

- contract tests cho mapping `RenderResult -> platform publish command`
- unit tests cho account resolution theo `account_ref`
- artifact tests cho download video/thumbnail từ handoff refs
- adapter tests cho YouTube/Facebook input validation
- integration tests cho multi-target publish report

#### Not acceptable

- không coi manual scripts là test suite
- không tiếp tục để `tests/` chỉ chứa script chạy tay

### 7.9 Documentation and operation changes

#### Required

- viết lại README đúng runtime thực tế
- có runbook:
  - local run
  - service run
  - retry/replay
  - credential setup
  - browser profile setup nếu vẫn còn cần
- có docs contract versioning cho publisher worker

## 8. Cấu trúc thư mục đề xuất cho `uploader8`

Mục tiêu là solid nhưng simple, không over-engineer.

```text
uploader8/
├── README.md
├── pyproject.toml
├── scripts/
│   ├── run_worker.sh
│   └── login_youtube.sh
├── deploy/
│   ├── uploader8.service
│   └── uploader8.timer
├── src/
│   └── uploader8/
│       ├── config.py
│       ├── app.py
│       ├── worker/
│       │   ├── consumer.py
│       │   ├── dispatcher.py
│       │   └── producer.py
│       ├── contracts/
│       │   ├── render_result.py
│       │   └── publish_result.py
│       ├── accounts/
│       │   └── registry.py
│       ├── artifacts/
│       │   └── dropbox_store.py
│       ├── platforms/
│       │   ├── youtube_adapter.py
│       │   └── facebook_adapter.py
│       └── observability/
│           ├── logging.py
│           └── metrics.py
└── tests/
    ├── test_contract_mapping.py
    ├── test_dispatcher.py
    ├── test_youtube_adapter.py
    └── test_facebook_adapter.py
```

Nguyên tắc:

- transport ở `worker/`
- contract ở `contracts/`
- artifact resolution ở `artifacts/`
- publish execution ở `platforms/`
- account resolution ở `accounts/`
- test nằm ở `tests/`
- không để logs, credentials artifacts, venv, manual state trong source tree

## 9. Checklist rename và migration

### 9.1 Phase 1: Freeze legacy

- đóng băng `xUploader` cũ, không thêm feature mới vào branch legacy
- chụp lại current behavior và các dependency thực sự còn dùng
- xác nhận clean ownership giữa `editor8`, `maker8`, `uploader8`

### 9.2 Phase 2: Rename

- rename repo/folder `xUploader` -> `uploader8`
- rename service/timer/script/docs theo naming mới
- rewrite README theo `uploader8`

### 9.3 Phase 3: Re-contract

- tạo input contract chuẩn từ `video.render.result.v1`
- map `publish_targets[]` thành internal publish commands
- bỏ DTO legacy `publishing_channel` / `article_dir` / `channel_name` / `profile`

### 9.4 Phase 4: Re-platform

- tạo dispatcher theo `platform`
- viết lại YouTube adapter theo `account_ref`
- viết lại Facebook adapter theo `account_ref`
- tách artifact resolution khỏi platform adapter

### 9.5 Phase 5: Cutover

- run uploader8 trên Kafka consumer path
- emit `video.publish.result.v1`
- test E2E với:
  - YouTube only
  - Facebook only
  - YouTube + Facebook cùng một job
- chỉ sau khi cutover ổn định mới retire flow legacy hoàn toàn

## 10. Checklist cleanup để loại bỏ legacy drift

Các mục sau phải được review và loại bỏ hoặc chuyển đúng chỗ:

- RabbitMQ consumer path trong publisher worker chính
- DTO legacy fields:
  - `publishing_channel`
  - `dbx_article_dir`
  - `article_dir`
  - `channel_name`
  - `profile`
  - `fb_url`
- README sai runtime
- `fastapi` / `uvicorn` / `starlette` nếu không có HTTP API thật
- manual scripts giả danh test:
  - `test.py`
  - `tests/test.py`
  - `tests/clean_dbox.py`
- refactor docs dang dở hoặc backup docs:
  - `PLAN.MD`
  - `PLAN.MD.bak`
  - `YOUTUBE_UPLOADER_TASKS.md`
  - `youtuber/REFACTORING_UPLOADER.md`
- tracked runtime artifacts:
  - `.env`
  - `auth_result.pickle`
  - `dbx8/auth_result.pickle`
  - `logs/`
  - `venv/`
  - `__pycache__/`
- hard-coded machine-specific paths
- code path xóa Dropbox artifact quá sớm

## 11. Definition of Done

Yêu cầu này chỉ được coi là done khi đồng thời đạt các điều kiện sau:

1. `xUploader` đã được rename hoàn toàn thành `uploader8`
2. `uploader8` consume được `video.render.result.v1`
3. `uploader8` emit được `video.publish.result.v1`
4. `uploader8` publish được theo `publish_targets[]` của `maker8`
5. `account_ref` là identity chuẩn xuyên `editor8`, `maker8`, `uploader8`
6. không còn dependency runtime chính vào payload legacy kiểu `channel_name`, `profile`, `article_dir`
7. artifact cleanup không phá retry, audit hoặc multi-target publish
8. có automated tests cho contract mapping và multi-target publish flow
9. README, runbook, service naming và folder naming đều dùng `uploader8`
10. legacy code, dead code, redundant code và repo residue đã được dọn về mức chấp nhận được

## 12. Kết luận cuối cùng

`xUploader` hiện tại chưa phải là component phù hợp để nối thẳng vào flow chuẩn của `editor8` và `maker8`. Hướng đúng không phải là đổi tên rồi giữ nguyên logic cũ, mà là:

- rename `xUploader` thành `uploader8`
- thay contract sang handoff chuẩn của `maker8`
- thay identity sang `account_ref`
- thay transport sang Kafka
- bỏ DTO legacy và cleanup residue

Chỉ khi hoàn thành cả 5 việc này, hệ thống mới đạt được flow nhất quán:

`editor8 -> maker8 -> uploader8 -> YouTube, Facebook`
