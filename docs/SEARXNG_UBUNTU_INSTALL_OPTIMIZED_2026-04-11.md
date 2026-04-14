# SearXNG Ubuntu Installation & Optimized Configuration (2026-04-11)

## 1. Kết quả triển khai

SearXNG đã được cài và chạy thành công trên Ubuntu host này theo mô hình Docker Compose.

- Host OS: `Ubuntu 24.04.4 LTS`
- SearXNG image: `docker.io/searxng/searxng:latest`
- Runtime version (container log): `2026.4.10-7737a0da1`
- Stack services:
  - `searxng-core`
  - `searxng-valkey`
- Bind endpoint (local-only): `http://127.0.0.1:8888`

Kiểm tra sau triển khai:

- `GET /` -> `200`
- `GET /search?q=linux&format=json` -> `200`

## 2. Kiến trúc cài đặt

Thư mục triển khai:

- `/home/beou/searxng/docker-compose.yml`
- `/home/beou/searxng/.env`
- `/home/beou/searxng/core-config/settings.yml`
- `/home/beou/searxng/core-config/limiter.toml`

Mô hình:

1. `searxng-core` phục vụ web/API.
2. `searxng-valkey` phục vụ storage cho limiter / cache-related features.
3. Port public được bind vào loopback (`127.0.0.1`) để tránh lộ ra Internet mặc định.

Lưu ý quyền file:

- Khi `FORCE_OWNERSHIP=true`, container sẽ đổi owner config sang user `searxng` (UID/GID `977`).
- Nếu host user không sửa được file trong `core-config`, chỉnh bằng:

```bash
cd /home/beou/searxng
docker compose exec --user root core sh -lc 'vi /etc/searxng/settings.yml'
docker compose restart core
```

## 3. Cấu hình tối ưu đã áp dụng

## 3.1 `.env` (runtime-level)

Các điểm chính:

- `SEARXNG_HOST=127.0.0.1`
- `SEARXNG_PORT=8888`
- `SEARXNG_BIND_ADDRESS=0.0.0.0`
- `SEARXNG_BASE_URL=http://127.0.0.1:8888/`
- `SEARXNG_SECRET=<random-64-hex>`
- `SEARXNG_VALKEY_URL=valkey://searxng-valkey:6379/0`
- `SEARXNG_IMAGE_PROXY=true`
- `SEARXNG_PUBLIC_INSTANCE=false`
- `SEARXNG_LIMITER=false` (xem giải thích bên dưới)

## 3.2 `settings.yml` (application-level)

Đã chỉnh theo profile private instance:

- `use_default_settings: true`
- `search.formats`: bật `html`, `json`, `rss`
- `search.autocomplete: duckduckgo`
- `server.public_instance: false`
- `server.image_proxy: true`
- `server.method: GET`
- `outgoing.request_timeout: 4.0`
- `enable_http2: true`
- giữ các plugin hữu ích: calculator/hash/unit_converter/tracker_url_remover...

## 3.3 Vì sao `SEARXNG_LIMITER=false`

Trong mô hình truy cập trực tiếp localhost (không reverse proxy), limiter có thể chặn request tool/script (đặc biệt khi thiếu header hoặc user-agent), gây `429 Too Many Requests` dù instance nội bộ.

Với nhu cầu máy local/private, cấu hình tối ưu thực dụng là:

- tắt limiter để ổn định API automation
- vẫn giữ bind localhost để giảm bề mặt tấn công

Nếu mở public qua reverse proxy, cần bật lại limiter (xem mục 8).

## 4. Lệnh vận hành

Từ thư mục `/home/beou/searxng`:

```bash
# start/update runtime env changes
docker compose up -d

# stop
docker compose down

# restart core
docker compose restart core

# status
docker compose ps

# logs
docker compose logs -f core
```

## 5. Lệnh kiểm tra nhanh (smoke test)

```bash
curl -sS -o /dev/null -w 'home=%{http_code}\n' http://127.0.0.1:8888/
curl -sS -o /dev/null -w 'json=%{http_code}\n' 'http://127.0.0.1:8888/search?q=linux&format=json'
```

Test JSON payload:

```bash
curl -sS 'http://127.0.0.1:8888/search?q=searxng&format=json' | jq '.query, (.results|length)'
```

## 6. Cập nhật SearXNG an toàn

```bash
cd /home/beou/searxng
docker compose down
docker compose pull
docker compose up -d
```

Sau update:

```bash
docker compose ps
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8888/
```

## 7. Backup/restore

## 7.1 Backup

```bash
cd /home/beou

tar czf searxng-backup-$(date +%F).tar.gz \
  searxng/.env \
  searxng/docker-compose.yml \
  searxng/core-config
```

## 7.2 Restore

1. Restore file vào đúng cấu trúc `/home/beou/searxng`.
2. Chạy lại:

```bash
cd /home/beou/searxng
docker compose up -d
```

## 8. Khi cần public instance (khuyến nghị hardening)

Nếu muốn expose ra Internet:

1. Đặt reverse proxy (Nginx/Caddy/Traefik) trước SearXNG.
2. Bật lại limiter:

```bash
# .env
SEARXNG_LIMITER=true
SEARXNG_PUBLIC_INSTANCE=true
```

3. Cập nhật `core-config/limiter.toml`:

- khai báo `trusted_proxies` đúng CIDR của reverse proxy
- không để rỗng trong mô hình public

4. Bật TLS và security headers tại reverse proxy.
5. Theo dõi logs lỗi engine / abuse patterns.

## 9. Sự cố đã gặp trong quá trình cài

1. `429 Too Many Requests` khi bật limiter ở local-direct mode.
- Nguyên nhân: bot/abuse protection không phù hợp luồng local script/curl.
- Cách xử lý đã áp dụng: `SEARXNG_LIMITER=false` cho profile private local.

2. Cảnh báo/lỗi từ một số engine upstream (`HTTP 403`, timeout, engine inactive).
- Đây là tình trạng thường gặp ở metasearch (phụ thuộc engine bên ngoài).
- Không làm hỏng toàn bộ instance; kết quả vẫn trả bình thường từ các engine khác.

## 10. Nguồn tham chiếu chính thức

- SearXNG container install docs:
  - https://docs.searxng.org/admin/installation-docker
- SearXNG settings reference:
  - https://docs.searxng.org/admin/settings/
- Container templates:
  - https://raw.githubusercontent.com/searxng/searxng/master/container/docker-compose.yml
  - https://raw.githubusercontent.com/searxng/searxng/master/container/.env.example
