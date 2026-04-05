# mcpserver8 – MCP Playwright Server (Detailed Design)

Tài liệu thiết kế chi tiết cho **mcpserver8** – một MCP Server độc lập chạy trên Linux host, điều khiển trình duyệt Chromium ở chế độ **Headed** (có giao diện thật) thông qua Playwright. Hệ thống phục vụ đa mục đích: **tìm kiếm web**, **tìm kiếm media**, và **upload video/nội dung** lên các nền tảng mạng xã hội. Mức độ chi tiết đảm bảo có thể implement ngay.

---

## 1. Tổng quan & Vai trò (System Role)

mcpserver8 là một **dịch vụ chạy thường trực (daemon)** trên Linux host. Nó expose các Tool qua giao thức MCP (HTTP/SSE) để bất kỳ MCP Client nào (Editor8, Publisher8, hoặc AI Agent) đều có thể gọi tới.

**Khác biệt chính so với bản thiết kế cũ (mcp-uploader):**

| Tiêu chí | mcp-uploader (cũ) | mcpserver8 (mới) |
|---|---|---|
| Phạm vi | Chỉ upload video | Upload + Search Web + Search Media + Audit |
| Môi trường chạy | Docker container | Linux host trực tiếp (systemd service) |
| Browser mode | Headless (qua Xvfb) | **Headed** – hiển thị cửa sổ thật trên desktop |
| Giao diện quản trị | Không có | Web UI Dashboard (FastAPI + Jinja2) |
| Tên project | `mcp-uploader/` | `mcpserver8/` |

**Luồng giao tiếp:**
```
MCP Clients (editor8 / publisher8 / AI Agent)
    │
    │  HTTP/SSE (JSON-RPC theo chuẩn MCP)
    ▼
┌─────────────────────────────────────────┐
│  mcpserver8                             │
│  ┌───────────────┐  ┌────────────────┐  │
│  │  MCP Server   │  │  Web UI (Mgmt) │  │
│  │  (SSE :8931)  │  │  (HTTP :8932)  │  │
│  └───────┬───────┘  └───────┬────────┘  │
│          │                  │           │
│  ┌───────▼──────────────────▼────────┐  │
│  │     Core Engine                   │  │
│  │  ┌──────────┐ ┌────────────────┐  │  │
│  │  │ Context  │ │   Audit DB     │  │  │
│  │  │ Manager  │ │  (SQLite)      │  │  │
│  │  └────┬─────┘ └────────────────┘  │  │
│  │       │                           │  │
│  │  ┌────▼─────────────────────────┐ │  │
│  │  │  Playwright (Headed Chrome)  │ │  │
│  │  └──────────────────────────────┘ │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

---

## 2. Technology Stack

| Component | Technology | Rationale |
|---|---|---|
| Ngôn ngữ | Python 3.11+ | Thống nhất với hệ sinh thái maker8 |
| MCP SDK | `mcp` (FastMCP) | Thư viện chính thức, decorator-based |
| Browser | `playwright` (Headed) | Chạy trực tiếp trên Linux desktop, không cần Xvfb |
| Anti-Bot | `playwright-stealth` | Ẩn fingerprint `navigator.webdriver` |
| Web UI | FastAPI + Jinja2 | Dashboard quản trị & Audit log viewer |
| Database | SQLite (via `aiosqlite`) | Nhẹ, không cần setup server DB riêng |
| Logging | `structlog` | Structured JSON logging |
| Process Mgmt | `systemd` user service | Tự khởi động lại, quản lý lifecycle |

---

## 3. Danh sách MCP Tools (Tool Registry)

mcpserver8 expose **3 nhóm Tool** qua giao thức MCP:

### Nhóm A – Web Search (Tìm kiếm Web)

| Tool Name | Mục đích | Input chính |
|---|---|---|
| `search_google_text` | Tìm kiếm văn bản qua Google Search | `query`, `num_results`, `lang` |
| `search_google_images` | Tìm kiếm hình ảnh qua Google Images | `query`, `num_results`, `safe_search` |
| `search_youtube_videos` | Tìm kiếm video trên YouTube | `query`, `num_results`, `sort_by` |
| `scrape_page_content` | Đọc nội dung text của một URL | `url` |

### Nhóm B – Upload (Đăng nội dung)

| Tool Name | Mục đích | Input chính |
|---|---|---|
| `upload_to_youtube` | Upload video lên YouTube (Shorts/Standard) | `profile_id`, `video_path`, `title`, `description`, `visibility` |
| `upload_to_tiktok` | Upload video lên TikTok | `profile_id`, `video_path`, `title`, `cover_offset_sec` |
| `upload_to_facebook` | Upload video lên Facebook Page | `profile_id`, `video_path`, `caption`, `page_name` |

### Nhóm C – Quản lý Session & Health

| Tool Name | Mục đích | Input chính |
|---|---|---|
| `check_profile_health` | Kiểm tra Session còn sống không | `profile_id`, `platform` |
| `list_profiles` | Liệt kê tất cả Profile đang quản lý | _(none)_ |
| `take_screenshot` | Chụp màn hình browser hiện tại | `profile_id` |

---

## 4. Kiến trúc Component chi tiết

### 4.1 Layer 1: MCP Transport (server.py)
- Khởi tạo `FastMCP(name="mcpserver8")`.
- Chạy SSE transport trên port `8931`.
- Đăng ký tất cả Tools bằng decorator `@mcp.tool()`.
- Chạy song song Web UI server (FastAPI) trên port `8932` trong cùng asyncio event loop.

### 4.2 Layer 2: Browser Context Manager (context_manager.py)

Quản lý **Persistent Browser Contexts** theo từng Profile ID.

**Thiết kế Pool:**
```python
class BrowserPool:
    """
    Quản lý tập hợp các Browser Context đang mở.
    Mỗi profile_id ánh xạ tới 1 Persistent Context duy nhất.
    """
    _contexts: dict[str, BrowserContext]  # profile_id -> context
    _playwright: Playwright
    _browser_type: BrowserType            # chromium
    _max_contexts: int = 5                # Giới hạn RAM
    
    async def acquire(self, profile_id: str, proxy_url: str = None) -> tuple[BrowserContext, Page]:
        """Lấy hoặc tạo Context cho profile. Áp dụng stealth tự động."""
        
    async def release(self, profile_id: str) -> None:
        """Đóng Context khi không dùng (giải phóng RAM)."""
        
    async def shutdown(self) -> None:
        """Đóng toàn bộ khi server shutdown."""
```

**Chi tiết `acquire()`:**
1. Nếu `profile_id` đã có trong `_contexts` → trả về context cũ (tái sử dụng).
2. Nếu số context đang mở >= `_max_contexts` → đóng context ít dùng nhất (LRU eviction).
3. Tạo context mới bằng `launch_persistent_context()`:
   - `user_data_dir` = `~/.mcpserver8/profiles/<profile_id>`
   - `headless=False` (Headed, hiển thị cửa sổ thật)
   - `args`: `--disable-blink-features=AutomationControlled`, `--disable-infobars`
   - Áp dụng `stealth_async(page)` ngay sau khi tạo page.

### 4.3 Layer 3: Scripts (scripts/{platform}.py)

Mỗi file chứa các hàm async thuần túy, nhận `Page` object và thực thi thao tác DOM.

**Quy tắc viết script (BẮT BUỘC):**
- ✅ Chỉ dùng: `page.get_by_role()`, `page.get_by_text()`, `page.get_by_label()`, `page.get_by_placeholder()`
- ❌ Tuyệt đối không dùng CSS selector hoặc XPath cứng
- ✅ Mỗi bước đều có `page.wait_for_load_state("networkidle")` hoặc timeout rõ ràng
- ✅ Chụp screenshot khi gặp lỗi, encode base64 trả về cho caller

### 4.4 Layer 4: Audit Database (audit_db.py)

SQLite database lưu tại `~/.mcpserver8/audit.db`.

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
    tool_name   TEXT    NOT NULL,            -- VD: 'upload_to_tiktok'
    profile_id  TEXT,
    input_json  TEXT    NOT NULL,            -- JSON đầu vào (đã redact password)
    status      TEXT    NOT NULL DEFAULT 'RUNNING',  -- RUNNING | SUCCESS | FAILED | CAPTCHA
    result_json TEXT,                        -- JSON đầu ra (URL bài đăng, error...)
    duration_ms INTEGER,
    screenshot  TEXT                         -- Base64 screenshot nếu lỗi
);

CREATE TABLE IF NOT EXISTS profiles (
    profile_id  TEXT PRIMARY KEY,
    platform    TEXT    NOT NULL,            -- youtube | tiktok | facebook
    label       TEXT,                        -- Tên hiển thị (VD: "Kênh Mèo Cute")
    proxy_url   TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    last_health TEXT                         -- Kết quả check gần nhất
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_status ON audit_log(status);
```

### 4.5 Layer 5: Web UI Dashboard (web_ui.py)

FastAPI app phục vụ trên port `8932`. Giao diện quản trị dùng Jinja2 template + HTMX.

**Các trang:**

| Route | Chức năng |
|---|---|
| `GET /` | Dashboard tổng quan (số job hôm nay, tỷ lệ thành công) |
| `GET /audit` | Bảng Audit Log (filter theo status/platform/thời gian) |
| `GET /audit/{id}` | Chi tiết một Audit entry (kèm screenshot nếu có) |
| `GET /profiles` | Danh sách Profiles (trạng thái session) |
| `POST /profiles` | Thêm Profile mới |
| `POST /profiles/{id}/health-check` | Trigger kiểm tra session |
| `POST /audit/{id}/retry` | Re-trigger một job đã fail |

**Authentication:** HTTP Basic Auth – 1 tài khoản admin duy nhất, cấu hình qua biến môi trường `MCPSERVER8_ADMIN_USER` / `MCPSERVER8_ADMIN_PASS`.

---

## 5. Cấu trúc thư mục mã nguồn (Project Structure)

```
mcpserver8/
├── pyproject.toml
├── README.md
├── src/
│   ├── __init__.py
│   ├── server.py               # Entrypoint: khởi tạo FastMCP + Web UI
│   ├── config.py               # Pydantic Settings (env vars, paths)
│   ├── context_manager.py      # BrowserPool: quản lý Persistent Contexts
│   ├── audit_db.py             # SQLite Audit DB (aiosqlite)
│   ├── web_ui.py               # FastAPI Dashboard (Jinja2 + HTMX)
│   ├── auth.py                 # HTTP Basic Auth middleware
│   ├── scripts/
│   │   ├── __init__.py
│   │   ├── google_search.py    # search_google_text, search_google_images
│   │   ├── youtube_search.py   # search_youtube_videos
│   │   ├── scraper.py          # scrape_page_content
│   │   ├── tiktok.py           # upload_to_tiktok
│   │   ├── youtube.py          # upload_to_youtube
│   │   └── facebook.py         # upload_to_facebook
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── dom_helpers.py      # safe_click, safe_fill, wait_and_screenshot
│   │   └── logger.py           # structlog config
│   └── templates/              # Jinja2 HTML templates cho Web UI
│       ├── base.html
│       ├── dashboard.html
│       ├── audit_list.html
│       ├── audit_detail.html
│       └── profiles.html
└── tests/
    ├── test_context_manager.py
    └── test_audit_db.py
```

---

## 6. Configuration (Cấu hình)

Tất cả qua biến môi trường (prefix `MCPSERVER8_`), quản lý bằng `pydantic-settings`:

| Variable | Default | Mô tả |
|---|---|---|
| `MCPSERVER8_MCP_PORT` | `8931` | Port cho MCP SSE transport |
| `MCPSERVER8_WEB_PORT` | `8932` | Port cho Web UI Dashboard |
| `MCPSERVER8_PROFILES_DIR` | `~/.mcpserver8/profiles` | Thư mục lưu Browser Profiles |
| `MCPSERVER8_AUDIT_DB` | `~/.mcpserver8/audit.db` | Đường dẫn SQLite Audit DB |
| `MCPSERVER8_MAX_CONTEXTS` | `5` | Số Browser Context tối đa đồng thời |
| `MCPSERVER8_ADMIN_USER` | `admin` | Username cho Web UI |
| `MCPSERVER8_ADMIN_PASS` | `changeme` | Password cho Web UI |
| `MCPSERVER8_LOG_LEVEL` | `INFO` | Mức log |
| `MCPSERVER8_DEFAULT_PROXY` | _(trống)_ | Proxy mặc định cho tất cả Profile |

---

## 7. Triển khai & Process Management

### 7.1 Cài đặt trực tiếp trên Linux host

```bash
# Clone và cài đặt
cd /home/beou/IdeaProjects/maker8/mcpserver8
python -m venv .venv
source .venv/bin/activate
pip install -e "."
playwright install chromium

# Chạy thủ công (development)
python -m src.server
```

### 7.2 Chạy như systemd service (production)

```ini
# ~/.config/systemd/user/mcpserver8.service
[Unit]
Description=mcpserver8 - MCP Playwright Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/beou/IdeaProjects/maker8/mcpserver8
Environment=DISPLAY=:0
Environment=MCPSERVER8_ADMIN_PASS=my-secure-password
ExecStart=/home/beou/IdeaProjects/maker8/mcpserver8/.venv/bin/python -m src.server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable mcpserver8
systemctl --user start mcpserver8
journalctl --user -u mcpserver8 -f   # Xem log
```

> **Lưu ý quan trọng**: Biến `DISPLAY=:0` bắt buộc phải trỏ tới X Server đang chạy trên host để Playwright mở cửa sổ Chromium thật.

---

## 8. Thách thức & Giải pháp (Challenges & Mitigations)

### 8.1 Selector Fragility (DOM thay đổi liên tục)
- **Vấn đề:** Facebook/YouTube/TikTok dùng React sinh class ngẫu nhiên.
- **Giải pháp:** BẮT BUỘC dùng Accessibility Selectors (`get_by_role`, `get_by_text`, `get_by_label`). TUYỆT ĐỐI KHÔNG dùng CSS selector hoặc XPath cứng.

### 8.2 CAPTCHA & Checkpoint
- **Vấn đề:** Hệ thống anti-bot phát hiện automation.
- **Giải pháp:**
  1. Dùng `playwright-stealth` + Persistent Profile (giữ fingerprint ổn định).
  2. Proxy IP cố định theo profile (tránh IP rotation bất thường).
  3. Khi dính CAPTCHA: chụp screenshot, lưu Audit log status = `CAPTCHA`, gửi notification. Vì browser chạy Headed trên host, admin có thể **nhìn thấy trực tiếp** cửa sổ browser và giải CAPTCHA bằng tay.

### 8.3 RAM Management (Headed Browser)
- **Vấn đề:** Mỗi Chromium context chiếm 400MB–1GB RAM.
- **Giải pháp:** `BrowserPool` giới hạn `max_contexts` (mặc định 5). Khi vượt quá, context ít dùng nhất bị đóng (LRU). Context sẽ được mở lại tự động khi cần.

### 8.4 Bảo mật Session
- **Vấn đề:** Thư mục Profile chứa cookies và session tokens nhạy cảm.
- **Giải pháp:** Đặt quyền `chmod 700` cho thư mục `~/.mcpserver8/profiles/`. Web UI bảo vệ bằng Basic Auth. Không expose port `8931`/`8932` ra Internet (chỉ bind localhost hoặc LAN nội bộ).
