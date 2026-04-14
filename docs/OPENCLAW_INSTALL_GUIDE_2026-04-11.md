# OpenClaw Installation Guideline (Cập nhật 2026-04-11)

## 1. Mục tiêu

Tài liệu này hướng dẫn cài OpenClaw theo luồng thực tế cho đội kỹ thuật (dev laptop, server, Docker), kèm các cập nhật mới nhất tại thời điểm **2026-04-11**.

## 2. Cập nhật mới nhất cần biết trước khi cài

- **Latest stable hiện tại**: `2026.4.10`.
- **Ngày phát hành**: `2026-04-11` (GitHub release tag `v2026.4.10`).
- **NPM dist-tags tại thời điểm kiểm tra**:
  - `latest`: `2026.4.10`
  - `beta`: `2026.4.10`
- **Node.js requirement**:
  - Tối thiểu: `Node 22.14+`
  - Khuyến nghị: `Node 24.x`
- **Release channels**:
  - `stable` -> `latest` (khuyến nghị production)
  - `beta` -> ưu tiên beta, fallback về stable nếu beta không hợp lệ
  - `dev` -> chạy theo `main`, không khuyến nghị production
- **Điểm đáng chú ý ở bản mới**: tăng cường nhiều fix security/runtime (SSRF defenses, hardening tool execution boundaries, plugin/security validations, gateway startup reliability).

## 3. Chọn mô hình cài đặt

- **Workstation (macOS/Linux/WSL2/Windows)**: dùng installer script (nhanh nhất).
- **Đã có Node riêng**: dùng `npm/pnpm/bun`.
- **Triển khai server containerized**: dùng Docker flow.
- **Cần customize sâu hoặc contribute**: build từ source.

## 4. Prerequisites

1. Node `24.x` (hoặc `22.14+`).
2. Quyền cài package global (`npm -g`) hoặc dùng installer script.
3. API key/provider auth tương ứng cho model bạn chọn.
4. Nếu chạy Docker: Docker Engine/Desktop + Compose v2, RAM >= 2 GB.

### 4.1 Ubuntu hiện tại trên máy này (snapshot 2026-04-11)

- OS: `Ubuntu 24.04.4 LTS (noble)`
- Node hiện tại: `v22.12.0` (**thấp hơn yêu cầu tối thiểu `22.14+`**)
- npm hiện tại: `10.9.0`
- Trạng thái OpenClaw: **không cài** (đã dừng và gỡ theo yêu cầu “không cài, chỉ viết tài liệu”).

### 4.2 Checklist trước khi cài trên Ubuntu

1. Nâng Node lên `22.14+` (khuyến nghị `24.x`).
2. Xác nhận global npm prefix có quyền ghi.
3. Chốt trước channel cài (`stable` cho production).
4. Nếu server: chuẩn bị backup thư mục state `~/.openclaw/`.

## 5. Cài đặt nhanh (khuyến nghị)

### 5.1 macOS / Linux / WSL2

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

Nếu muốn cài mà chưa onboarding ngay:

```bash
curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard
```

### 5.1.1 Ubuntu (khuyến nghị thực thi theo thứ tự)

```bash
# 1) Kiểm tra version hiện tại
node -v
npm -v

# 2) Nếu Node < 22.14, nâng lên Node 24 (NodeSource)
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt-get install -y nodejs

# 3) Cài OpenClaw (không onboarding)
curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard

# 4) Verify
openclaw --version
openclaw doctor
openclaw gateway status
```

### 5.2 Windows (PowerShell)

```powershell
iwr -useb https://openclaw.ai/install.ps1 | iex
```

Không chạy onboarding ngay:

```powershell
& ([scriptblock]::Create((iwr -useb https://openclaw.ai/install.ps1))) -NoOnboard
```

## 6. Cài đặt thủ công (khi đã tự quản lý Node)

### 6.1 npm

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

### 6.2 pnpm

```bash
pnpm add -g openclaw@latest
pnpm approve-builds -g
openclaw onboard --install-daemon
```

### 6.3 bun

```bash
bun add -g openclaw@latest
openclaw onboard --install-daemon
```

### 6.4 từ source

```bash
git clone https://github.com/openclaw/openclaw.git
cd openclaw
pnpm install && pnpm ui:build && pnpm build
pnpm link --global
openclaw onboard --install-daemon
```

## 7. Verify sau cài đặt

```bash
openclaw --version
openclaw doctor
openclaw gateway status
openclaw dashboard
```

Mặc định dashboard local:

- `http://127.0.0.1:18789/`

## 8. Quản lý cập nhật chuẩn

### 8.1 Cập nhật thường xuyên

```bash
openclaw update
```

Chuyển channel:

```bash
openclaw update --channel stable
openclaw update --channel beta
openclaw update --channel dev
```

Dry-run trước khi update:

```bash
openclaw update --dry-run
```

### 8.2 Sau update

```bash
openclaw doctor
openclaw gateway restart
openclaw health
```

### 8.3 Pin version / rollback

```bash
npm i -g openclaw@<version>
openclaw doctor
openclaw gateway restart
```

Kiểm tra version mới nhất trên npm:

```bash
npm view openclaw version dist-tags --json
```

## 9. Docker guideline (khi triển khai container)

### 9.1 Setup chuẩn

```bash
./scripts/docker/setup.sh
```

Dùng pre-built image:

```bash
export OPENCLAW_IMAGE="ghcr.io/openclaw/openclaw:latest"
./scripts/docker/setup.sh
```

### 9.2 Health checks

```bash
curl -fsS http://127.0.0.1:18789/healthz
curl -fsS http://127.0.0.1:18789/readyz
```

### 9.3 Lưu ý persistence

Cần persist các thư mục state/workspace để không mất:

- `~/.openclaw/` (config, auth profiles, credentials)
- `~/.openclaw/workspace`

## 10. Troubleshooting nhanh

### 10.1 `openclaw: command not found`

```bash
node -v
npm prefix -g
echo "$PATH"
```

Nếu thiếu global bin trong `PATH`:

```bash
export PATH="$(npm prefix -g)/bin:$PATH"
```

### 10.2 `sharp` build lỗi (npm)

```bash
SHARP_IGNORE_GLOBAL_LIBVIPS=1 npm install -g openclaw@latest
```

### 10.3 Permission lỗi khi cài global

- Linux/macOS: fix quyền npm global prefix hoặc dùng version manager (`fnm`, `nvm`).
- Docker bind-mount lỗi quyền: đảm bảo owner đúng UID chạy container.

## 11. Uninstall sạch

```bash
openclaw uninstall
```

Non-interactive:

```bash
openclaw uninstall --all --yes --non-interactive
```

Nếu cần manual:

```bash
openclaw gateway stop
openclaw gateway uninstall
rm -rf "${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
```

## 12. Checklist production tối thiểu

1. Dùng channel `stable`.
2. Bật update policy có kiểm soát (dry-run + rollback plan).
3. Chạy `openclaw doctor` trong pipeline post-deploy.
4. Backup state directory có mã hóa.
5. Áp dụng allowlist cho channels/senders; không expose gateway token.
6. Monitor `healthz/readyz` và gateway restart loop.

## 13. Nguồn tham chiếu chính thức (đã kiểm tra 2026-04-11)

- Install: https://docs.openclaw.ai/install
- Node requirements: https://docs.openclaw.ai/install/node
- Updating: https://docs.openclaw.ai/install/updating
- Release channels: https://docs.openclaw.ai/install/development-channels
- Docker: https://docs.openclaw.ai/install/docker
- Uninstall: https://docs.openclaw.ai/install/uninstall
- GitHub releases: https://github.com/openclaw/openclaw/releases
- GitHub latest release API: https://api.github.com/repos/openclaw/openclaw/releases/latest
