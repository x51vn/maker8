# editor8 MCP Playwright + Google Search: Yêu Cầu Chi Tiết Và Kế Hoạch

## 1. Mục tiêu

Tài liệu này áp dụng quy tắc 2 bước:

1. Mở rộng tối đa bài toán để không bỏ sót yêu cầu ngầm.
2. Cô động lại thành một khung kiến trúc khả thi, ít blast radius, phù hợp với hiện trạng `editor8`.

Mục tiêu nghiệp vụ:

- Bổ sung interface/implementation để `editor8` kết nối được nhiều `mcp-server`.
- Implement kết nối `MCP Playwright` cho `editor8`.
- Dùng `MCP Playwright` để bổ sung search provider cho `text`, `image`, `video`, lấy kết quả từ Google.
- Bổ sung chỗ cấu hình MCP trên giao diện `editor8`, lưu cấu hình vào database.
- Có kế hoạch triển khai rõ ràng, tách được phần control plane và execution plane.

## 2. Hiện trạng codebase và các điểm bám có sẵn

Những điểm đã có trong `editor8` và nên tận dụng:

- `backend/src/editor8/tools/base.py` đã có `TextSearchProvider` và `TextSearchService`, hỗ trợ nhiều provider chạy song song, merge và deduplicate.
- `backend/src/editor8/assets/base.py` và `backend/src/editor8/assets/service.py` đã có `AssetProvider` và `AssetSearchService`, cũng theo mô hình pluggable provider.
- `backend/src/editor8/tools/providers/__init__.py` đã có text providers mặc định: DuckDuckGo, Brave, SerpAPI, Baidu, Wikipedia.
- `backend/src/editor8/assets/providers/__init__.py` đã có asset providers mặc định: `icrawler`, Pexels, Unsplash, Pixabay, yt-dlp.
- `backend/src/editor8/agents/factory.py` giữ contract agent-facing ổn định qua 3 tool hiện có: `text_search`, `image_search`, `video_search`. Đây là boundary đúng để cắm provider mới mà không đổi prompt/tool contract của agent.
- `backend/src/editor8/pipeline/orchestrator.py` cho thấy media search đang chạy trong pipeline worker, không chạy trong frontend.
- `backend/src/editor8/worker.py` cho thấy `editor8` đã tách worker riêng. Điều này rất quan trọng: MCP execution phải sống chủ yếu ở worker, không phải ở FastAPI app.
- `backend/src/editor8/models/database.py` đã có:
  - `AppSetting`: key-value config.
  - `Integration`: external connection theo kiểu token/account.
  - `Channel`: cấu hình target xuất bản.
- `backend/src/editor8/api/routes.py` + `services/app_settings.py` + `frontend/src/app/settings/page.tsx` đã có luồng UI/API/DB cho settings.
- `backend/src/editor8/api/dropbox_routes.py` + `frontend/src/app/dropbox/page.tsx` đã có pattern UI/API cho một integration thực tế.
- `frontend/src/components/ui/Sidebar.tsx` đã có navigation riêng cho `Channels`, `Dropbox`, `Settings`; có thể thêm một mục MCP mà không làm lệch layout hiện tại.

Các khoảng trống hiện tại:

- Chưa có abstraction nào cho MCP client, transport, lifecycle, capability discovery, tool execution.
- Chưa có nơi quản trị nhiều MCP server trong UI.
- `Integration` hiện phù hợp kiểu "mỗi provider một connection theo user", chưa phù hợp quản trị nhiều MCP server với command/args/env/tool allowlist.
- `AppSetting` phù hợp global key-value nhỏ, không phù hợp làm nơi chính để chứa danh sách MCP server nhiều field, nhiều trạng thái.
- `TextSearchService` và `AssetSearchService` đã pluggable, nhưng chưa có provider nào dựa trên MCP.
- Quick scan migration hiện tại không thấy migration tạo `app_settings`; ORM đã có model nhưng discipline giữa ORM và Alembic ở phần này chưa rõ. Vì vậy không nên tiếp tục nhồi bài toán MCP vào `app_settings`.

## 3. Bước 1: Mở rộng tối đa yêu cầu

### 3.1. Nền tảng kết nối MCP server

`editor8` cần một lớp MCP platform nội bộ chứ không chỉ một đoạn code nối Playwright.

Yêu cầu chức năng:

- Quản lý nhiều MCP server, không khóa vào riêng Playwright.
- Hỗ trợ ít nhất transport `stdio` cho phase đầu; thiết kế interface phải đủ chỗ cho `streamable_http` hoặc `sse` ở phase sau.
- Có lifecycle rõ ràng:
  - load config từ DB
  - connect
  - health check
  - list tools/capabilities
  - call tool
  - reconnect/backoff
  - shutdown
- Cho phép enable/disable từng server.
- Cho phép khai báo purpose/capability của server:
  - browser_automation
  - google_text_search
  - google_image_search
  - google_video_search
  - generic_tooling
- Có timeout, retry, concurrency limit theo server.
- Có tool allowlist để không mở toàn bộ tool MCP cho agent/runtime ngoài ý muốn.
- Có health status quan sát được từ UI/API.
- Có cache capability list để không phải query lại liên tục.

Yêu cầu phi chức năng:

- Failure isolation: một MCP server lỗi không được làm vỡ cả pipeline.
- Không để API process giữ browser session dài hạn.
- Log rõ server nào, tool nào, query nào, duration bao lâu, lỗi gì.
- Secrets phải được che khi trả về API/UI.
- Thiết kế phải giữ được testability: mock được MCP client mà không cần browser thật.

### 3.2. MCP Playwright cho editor8

`MCP Playwright` ở đây là browser automation server để `editor8` có thể điều khiển browser qua MCP thay vì gọi Playwright trực tiếp trong code nghiệp vụ.

Yêu cầu cụ thể:

- Có một implementation `PlaywrightMCPClient` của interface MCP chung.
- Phase đầu ưu tiên `stdio`-based server process.
- Cấu hình cần quản lý được:
  - command
  - args
  - env
  - working directory nếu cần
  - headless/headful policy
  - browser/channel nếu server hỗ trợ
  - timeout mặc định
  - allowlist domain
  - allowlist tool
- Có lệnh test connection:
  - start server
  - handshake
  - list tools
  - chạy một smoke check an toàn
- Có domain restriction cho Google để tránh browser bị lạm dụng như generic web bot trong phase đầu.
- Có session strategy rõ ràng:
  - API chỉ tạo session ngắn cho test
  - Worker giữ session/pool để phục vụ search runtime

### 3.3. Search provider Google cho text/image/video qua MCP Playwright

Phần này không chỉ là "search Google và trả list". Nó phải khớp với contract hiện tại của `editor8`.

#### 3.3.1. Google text search

Yêu cầu:

- Implement `TextSearchProvider` mới dùng Playwright MCP để mở Google search và parse organic results.
- Trả về `TextSearchResult` chuẩn: `title`, `snippet`, `url`, `source`, `metadata`.
- Có query normalization riêng cho Google:
  - trim, collapse spaces
  - locale/lang
  - optional site filters nếu input có
- Có dedup URL.
- Có fallback an toàn nếu Google consent page/CAPTCHA xuất hiện: return `[]` + log có cấu trúc, không throw làm gãy pipeline.

#### 3.3.2. Google image search

Yêu cầu:

- Implement `AssetProvider` mới cho `asset_type=image`, dùng Google Images qua Playwright MCP.
- Trả về `AssetCandidate` khớp contract hiện tại.
- Kết quả phải cố gắng resolve:
  - source page URL
  - preview thumbnail
  - original image URL nếu lấy được
  - width/height nếu lấy được
- Chỉ emit candidate khi URL đủ usable cho pipeline downstream.
- Nếu chỉ lấy được page URL mà không lấy được direct image/original usable URL thì phải skip hoặc đánh dấu metadata rõ ràng, không phát tán candidate mơ hồ.

#### 3.3.3. Google video search

Yêu cầu:

- Implement `AssetProvider` mới cho `asset_type=video`, dùng Google Videos qua Playwright MCP.
- Chỉ trả candidate khi normalize được về `source_kind` mà pipeline hiện tại hiểu:
  - `youtube`
  - hoặc `http` direct video URL thực sự usable
- Nếu kết quả Google chỉ là trang web/video page nhưng không resolve được thành URL downstream dùng được thì bỏ qua.
- Metadata cần có nếu có thể:
  - source site
  - preview image
  - duration
  - channel/uploader

#### 3.3.4. Một lưu ý rất quan trọng về Google image/video

Đây là rủi ro nghiệp vụ và kỹ thuật lớn nhất:

- Google search không phải stock media API.
- Google Images/Google Videos thường trả kết quả discovery, không đảm bảo link là asset URL trực tiếp.
- `maker8`/pipeline downstream hiện kỳ vọng asset candidate hoặc là `http` asset URL usable, hoặc `youtube` URL có thể xử lý.
- Vì vậy phase triển khai phải xem Google media search là "provider discovery có kiểm soát", không được giả định mọi kết quả Google đều tải/render được.

Hệ quả bắt buộc:

- Provider Google cho image/video phải có bước "result normalization and usability filter".
- Existing providers như Pexels/Pixabay/yt-dlp vẫn phải giữ lại để làm fallback.

### 3.4. UI cấu hình MCP trong editor8

Nhu cầu UI không dừng ở một form nhập command.

Yêu cầu:

- Có màn hình riêng để quản trị MCP server, không nhét vào page `Settings` hiện tại theo kiểu 3 card.
- Hiển thị danh sách MCP server với các thông tin:
  - tên
  - loại server
  - transport
  - trạng thái bật/tắt
  - health status
  - last checked
  - capability tags
- Có CRUD:
  - tạo server mới
  - sửa
  - bật/tắt
  - test connection
  - xóa
- Có form cấu hình:
  - display name
  - server kind
  - transport
  - command/url
  - args
  - env
  - timeout
  - tool allowlist
  - domain allowlist
  - notes/description
- API không trả raw secret sau khi đã lưu.
- UI có trạng thái validate trước khi save.

### 3.5. Database persistence

Lưu vào database là yêu cầu bắt buộc, nhưng cần đúng chỗ.

Không nên dùng:

- `AppSetting` làm nơi chính cho danh sách server vì model này là flat key-value, không hợp cho entity sống lâu, có health state, nhiều trường, nhiều record.
- `Integration` làm nơi chính vì model này đang thiên về OAuth/account tokens theo provider-user unique, không phải generic server registry.

Yêu cầu dữ liệu tối thiểu cho MCP server:

- identity:
  - id
  - name
  - slug
  - kind
  - transport
- connection:
  - command
  - args_json
  - env_json
  - url
  - headers_json
  - working_dir
- control:
  - enabled
  - tool_allowlist_json
  - domain_allowlist_json
  - config_json
- runtime state:
  - health_status
  - last_health_checked_at
  - last_health_error
  - capabilities_json
  - last_connected_at
- ownership:
  - owner_user_id nullable nếu global, hoặc non-null nếu muốn per-user
- audit:
  - created_at
  - updated_at

Yêu cầu bảo mật:

- Secret values phải được mã hóa hoặc ít nhất được tách khỏi API response.
- UI chỉ hiển thị masked values sau khi lưu.

### 3.6. Luồng thực thi trong worker

Search hiện sống ở pipeline worker, nên MCP cũng phải khớp mô hình đó.

Yêu cầu:

- Worker load active MCP servers từ DB khi startup.
- Worker có registry cache và cơ chế refresh khi config đổi.
- Text/image/video providers lấy client từ registry, không spawn process mới cho mỗi query.
- Nếu MCP Playwright fail, provider phải degrade mềm:
  - log warning/error có cấu trúc
  - return `[]`
  - để provider khác tiếp tục chạy

### 3.7. Quan sát, vận hành, chất lượng

Yêu cầu observability:

- Logs:
  - `mcp.connect`
  - `mcp.disconnect`
  - `mcp.healthcheck`
  - `mcp.tool.call`
  - `mcp.google.search`
- Metrics:
  - latency theo server/tool
  - error count theo server/tool
  - result count theo modality
  - consecutive failures
- UI:
  - status OK/DEGRADED/ERROR
  - error message cuối cùng

Yêu cầu test:

- unit test cho registry/client abstraction
- unit test cho provider normalization/parsing
- integration test cho API CRUD và DB persistence
- smoke test cho Playwright MCP handshake
- regression test bảo đảm `text_search`, `image_search`, `video_search` vẫn hoạt động nếu MCP bị disable

## 4. Bước 2: Cô động lại thành khung kiến trúc hợp lý

### 4.1. Quyết định kiến trúc

Đề xuất chốt 5 quyết định sau:

- Giữ nguyên contract agent-facing hiện tại: agent vẫn chỉ biết `text_search`, `image_search`, `video_search`.
- Thêm lớp MCP core dùng chung, nhưng phase đầu chỉ ship 1 implementation thật: `PlaywrightMCPClient`.
- Control plane nằm ở API/UI/DB; execution plane nằm ở worker.
- Thêm entity DB mới cho MCP server; không overload `app_settings` hay `integrations`.
- Google MCP providers được thêm theo kiểu additive, không thay toàn bộ provider cũ ngay lập tức.

### 4.2. Khung module backend đề xuất

Đề xuất thêm các module sau trong `editor8/backend/src/editor8/`:

- `mcp/contracts.py`
  - `MCPServerConfig`
  - `MCPToolDescriptor`
  - `MCPHealthStatus`
  - `MCPCallResult`
- `mcp/client_base.py`
  - interface chung cho MCP client
- `mcp/registry.py`
  - load config từ DB
  - cache active clients
  - connect/reconnect/disconnect
- `mcp/clients/playwright.py`
  - implementation cho Playwright MCP
- `mcp/health.py`
  - smoke checks và health aggregation
- `mcp/service.py`
  - façade để provider/runtime gọi
- `tools/providers/google_mcp.py`
  - text provider qua Google + Playwright MCP
- `assets/providers/google_images_mcp.py`
  - image provider qua Google Images + Playwright MCP
- `assets/providers/google_videos_mcp.py`
  - video provider qua Google Videos + Playwright MCP
- `api/mcp.py`
  - CRUD + test connection + health endpoints

### 4.3. Khung database đề xuất

Đề xuất thêm bảng mới `mcp_servers`.

Lý do:

- Một record tương ứng một server definition.
- Phù hợp hơn `AppSetting` cho CRUD/list/filter/health.
- Phù hợp hơn `Integration` cho multi-server, multi-transport, multi-purpose.

Schema đề xuất tối thiểu:

| Column | Ý nghĩa |
| --- | --- |
| `id` | UUID |
| `name` | tên hiển thị |
| `slug` | khóa ổn định, unique |
| `server_kind` | `playwright`, `generic`, ... |
| `transport` | `stdio`, `streamable_http`, `sse` |
| `enabled` | bật/tắt |
| `command` | executable cho stdio |
| `args_json` | args list |
| `env_json` | env đã mask/encrypt |
| `url` | cho HTTP/SSE transport |
| `headers_json` | header config nếu cần |
| `working_dir` | cwd nếu cần |
| `tool_allowlist_json` | tool được phép dùng |
| `domain_allowlist_json` | domain được phép truy cập |
| `capability_tags_json` | ví dụ `google_text_search` |
| `config_json` | timeout, retry, locale, parser mode |
| `health_status` | `unknown`, `ok`, `degraded`, `error` |
| `last_health_checked_at` | timestamp |
| `last_health_error` | lỗi gần nhất |
| `last_connected_at` | timestamp |
| `owner_user_id` | nullable nếu global |
| `created_at` | audit |
| `updated_at` | audit |

Khuyến nghị:

- Nếu hệ thống chưa có secret encryption chuẩn, cần thêm lớp application-level encryption cho `env_json`/`headers_json` trước khi lưu DB.
- Nếu chưa làm được encryption ở phase 1, vẫn phải mask giá trị khi đọc ra API và giới hạn field nào được lưu.

### 4.4. Luồng runtime rút gọn

Luồng đề xuất:

1. Admin cấu hình MCP server trên UI.
2. API validate và lưu record vào `mcp_servers`.
3. Worker nạp danh sách active server khi startup hoặc refresh.
4. `TextSearchService`/`AssetSearchService` khởi tạo provider list, trong đó có các Google MCP providers nếu server tương ứng đang active.
5. Khi agent gọi `text_search` hoặc media search chạy trong pipeline:
   - provider lấy client từ registry
   - provider gọi Playwright MCP tools
   - parse DOM thành result schema
   - normalize và filter usability
   - trả kết quả theo contract hiện tại
6. Nếu MCP lỗi:
   - provider trả `[]`
   - service vẫn merge kết quả từ provider khác

### 4.5. UI rút gọn hợp lý

Đề xuất thêm route riêng: `/mcp`.

Lý do:

- `Settings` hiện tại là card-based key-value page, không hợp cho entity list + test connection + health state.
- `Dropbox` page là pattern tốt hơn cho một integration có status và thao tác cụ thể.
- Sidebar hiện đã có cấu trúc rõ ràng, việc thêm mục `MCP` là tự nhiên.

UI page `/mcp` nên có 3 khu:

- danh sách server
- form create/edit
- panel test connection + capability preview

Phase đầu chưa cần:

- visual workflow builder cho tool binding
- role-based permission chi tiết
- lịch sử audit đầy đủ theo từng lần tool call trên UI

### 4.6. Tích hợp với provider hiện tại

Đề xuất tích hợp tối thiểu:

- `TextSearchService`
  - thêm `GoogleMCPTextProvider`
- `AssetSearchService`
  - thêm `GoogleMCPImageProvider`
  - thêm `GoogleMCPVideoProvider`

Đề xuất policy enable:

- text: có thể enable sớm
- image: enable sau khi xác nhận normalize được original URL usable
- video: chỉ enable chính thức khi normalize ổn định về `youtube` hoặc `http` usable

## 5. Kế hoạch triển khai

### Phase 0: Chốt nền tảng và migration discipline

- Tạo spec cho `mcp_servers`.
- Thêm Alembic migration cho bảng mới.
- Rà lại discipline ORM/Alembic vì hiện quick scan chưa thấy migration cho `app_settings`.
- Thêm model SQLAlchemy + Pydantic schemas + API contracts.

Deliverable:

- entity `mcp_servers`
- CRUD API cơ bản
- unit test cho model/API validation

### Phase 1: MCP core + Playwright connector

- Implement `MCPClient` interface.
- Implement `PlaywrightMCPClient`.
- Implement registry/cache/healthcheck.
- Thêm endpoint `test connection`.
- Thêm worker bootstrap hook để load active MCP servers.

Deliverable:

- worker kết nối được Playwright MCP
- API test connection chạy được
- logs và health status cơ bản

### Phase 2: UI quản trị MCP

- Thêm route `/mcp`.
- Thêm sidebar item.
- Thêm list/create/edit/enable-disable/delete/test connection.
- Mask secrets trên UI.

Deliverable:

- admin thao tác full lifecycle MCP server từ giao diện
- config được lưu DB và load lại được

### Phase 3: Google text search provider qua MCP Playwright

- Implement provider mới cho `TextSearchProvider`.
- Parse Google organic results.
- Thêm feature flag hoặc active-by-config.
- Giữ provider cũ để fallback.

Deliverable:

- `text_search` dùng được Google qua MCP Playwright
- pipeline không gãy nếu provider lỗi

### Phase 4: Google image search provider qua MCP Playwright

- Implement provider mới cho `AssetProvider(image)`.
- Thêm normalization/resolution logic cho original image URL.
- Filter candidate không usable.

Deliverable:

- `image_search` trả `AssetCandidate` usable
- có metadata đủ để debug nguồn

### Phase 5: Google video search provider qua MCP Playwright

- Implement provider mới cho `AssetProvider(video)`.
- Normalize kết quả về `youtube` hoặc direct `http`.
- Bỏ qua kết quả không usable cho downstream.

Deliverable:

- `video_search` trả candidate phù hợp contract hiện tại
- không phát tán page URL vô nghĩa vào pipeline render

### Phase 6: Hardening

- metrics
- retry/backoff
- health UI
- parser robustness
- smoke tests
- rollout flag theo modality

Deliverable:

- vận hành được trong môi trường dài hạn
- quan sát được trạng thái degrade

## 6. Acceptance criteria

### AC-1. MCP platform

- `editor8` lưu được nhiều MCP server trong DB.
- API CRUD hoạt động.
- Có test connection và health status.

### AC-2. Playwright MCP

- Worker connect được tới Playwright MCP server.
- Có thể list tools/capabilities.
- Có timeout và reconnect policy cơ bản.

### AC-3. UI

- Có route UI riêng để quản lý MCP.
- Save xong reload lại vẫn thấy dữ liệu từ DB.
- Secret không bị echo raw ra UI/API.

### AC-4. Text search

- `text_search` trả kết quả Google qua MCP Playwright theo schema hiện tại.
- Nếu provider lỗi, system degrade mềm và provider khác vẫn chạy.

### AC-5. Image search

- `image_search` chỉ trả candidate usable cho pipeline.
- Candidate có tối thiểu `asset_id`, `url`, `preview_url`, `source_kind`, `metadata`.

### AC-6. Video search

- `video_search` chỉ trả candidate khi normalize được về `youtube` hoặc usable `http`.
- Không đẩy raw search result page không dùng được xuống assembler/render pipeline.

### AC-7. Vận hành

- Logs phân biệt được lỗi connect MCP, lỗi tool call, lỗi parse Google, lỗi usability filter.
- Có health status cho từng server trên UI.

## 7. Rủi ro và quyết định mở

### 7.1. Google scraping volatility

- Google có thể đổi DOM, consent flow, hoặc CAPTCHA.
- Đây là rủi ro lớn nhất của hướng Playwright MCP.
- Giải pháp: parser adapter riêng, feature flag, fallback provider giữ nguyên.

### 7.2. Media URL usability

- Image/video từ Google không mặc định tương đương stock-media API.
- Cần filter rất chặt trước khi biến thành `AssetCandidate`.

### 7.3. Bảo mật secret

- Không chấp nhận lưu raw secret và trả lại nguyên văn qua API.
- Cần chốt sớm encryption/masking policy.

### 7.4. Multi-user scope

- `editor8` hiện chưa có role model phong phú.
- Nếu deployment là single-admin, có thể để MCP config là global.
- Nếu multi-user là mục tiêu gần, cần bổ sung ownership/role semantics trước khi mở rộng.

## 8. Kết luận chốt

Khung hợp lý nhất cho yêu cầu hiện tại là:

- thêm một lớp `MCP core` tổng quát nhưng chỉ ship `Playwright` ở phase đầu,
- thêm bảng `mcp_servers` riêng để lưu cấu hình,
- thêm UI `/mcp` riêng thay vì nhét vào `Settings`,
- giữ nguyên contract agent-facing hiện tại,
- cắm Google MCP providers xuống lớp provider của `text_search` và `asset_search`,
- rollout theo thứ tự `text -> image -> video`,
- luôn giữ fallback providers cũ để tránh biến MCP Playwright thành single point of failure.

Đây là phương án vừa đáp ứng yêu cầu "kết nối nhiều MCP server", vừa triển khai được thực tế trên `editor8` hiện tại mà không phải phá vỡ pipeline/tool contract đang chạy.
