# Server2 LiteLLM Fallback Requirements

> Investigation date: 2026-04-19
>
> Investigated directly on: `root@10.113.213.1`

## 1. Muc tieu

Tai lieu nay xac dinh requirement de cau hinh lai LiteLLM tren `server2` sao cho cac model group chat production khong bi hard-fail khi upstream chinh gap quota, timeout, 5xx, hoac container trung gian bi loi.

Muc tieu uu tien:

- `editor8-gpt` khong duoc fail cung khi `github-copilot-svcs` gap 429/5xx.
- `llmproxy` khong duoc fail cung khi upstream GitHub/Copilot free-model rotation can quota.
- Cac model group chat con lai (`copilot-mini-chat`, `copilot-chat-mini`) phai co fallback doc lap, khong chi retry trong cung mot failure domain.
- Cau hinh routing/fallback phai co mot source of truth ro rang trong `deployment/server2/litellm/`.

## 2. Hien trang da xac nhan tren server

### 2.1 Runtime config hien tai

- File `/root/deployment/server2/litellm/litellm_config.yaml` hien chi co:
  - `general_settings`
  - `litellm_settings.drop_params: true`
- File YAML hien khong co:
  - `model_list`
  - `router_settings.fallbacks`
  - `context_window_fallbacks`
- Runtime dang doc model topology tu PostgreSQL vi `store_model_in_db=true`.
- Bang `LiteLLM_Config` hien chi co:
  - `general_settings`
  - `router_settings = { timeout: 6000, num_retries: 2, allowed_fails: 3, cooldown_time: 5, routing_strategy: usage-based-routing }`
- Log LiteLLM ngay 2026-04-19 xac nhan:
  - `fallbacks: None`
  - `context_window_fallbacks: None`

### 2.2 Model groups dang public

| Model group | So deployment | Upstream hien tai | Nhan xet |
|---|---:|---|---|
| `editor8-gpt` | 2 | `openai/gpt-5-mini` qua `http://github-copilot-svcs:7071/v1` | Ca 2 deployment cung mot `api_base`, khong doc lap failure domain |
| `llmproxy` | 2 | `github/gpt-5-mini` qua `http://10.113.213.1:7071/v1` | Van phu thuoc `github-copilot-svcs` |
| `copilot-mini-chat` | 2 | `github/gpt-5-mini` va `github/grok-code-fast-1` qua `:7071/v1` | Co replica, nhung van cung failure domain |
| `copilot-chat-mini` | 1 | `openai/gpt-5-mini` qua `http://github-copilot-svcs:7071/v1` | Chua co fallback doc lap |
| `gemini/gemini-3-flash-preview` | 2 | native Gemini (`G1`, `G2`) | La failure domain doc lap duy nhat dang thay ro |
| `test-model` | 1 | `openai/gpt-5.4` | Alias ten test, khong duoc xem la backup production hop le |

### 2.3 Evidence ve failure thuc te

- Log `github-copilot-svcs` ngay `2026-04-18 15:11:13` den `15:11:17` cho thay internal free-model rotation da can het va tra `HTTP 429`.
- Cac model bi exhaust trong log gom `oswe-vscode-prime`, `gpt-4.1`, `gpt-5-mini`.
- Sau khi het tat ca rotation noi bo, service da "forwarding final upstream response", nghia la request van fail o bien tren.
- Do LiteLLM dang `fallbacks=None`, moi model group chi dua vao `:7071/v1` van co kha nang fail cung o muc user-facing.

### 2.4 Van de drift cau hinh

- Tai lieu local/README hien de nguoi doc hieu nham rang YAML la source of truth.
- Thuc te runtime model topology dang nam trong DB.
- Neu chi sua `litellm_config.yaml` ma khong sua DB thi fallback se van khong co hieu luc.

## 3. Root cause

Root cause hien tai khong phai la LiteLLM bi down. Root cause la:

1. LiteLLM dang co retry/cooldown, nhung khong co `fallbacks`.
2. Nhieu model group chat production dang cung phu thuoc mot failure domain la `github-copilot-svcs:7071`.
3. Replica hien tai phan lon la "nhan ban cung upstream", khong phai backup doc lap.
4. Source of truth cho model routing dang bi tach giua file va DB, nen cau hinh fallback de bi thieu hoac bi drift.

## 4. Requirements bat buoc

### R1. Chon mot source of truth cho model topology

Phai chot 1 trong 2 huong va ghi ro trong implementation:

- Huong khuyen nghi: chuyen model topology ve file trong `deployment/server2/litellm/` bang cach bo `store_model_in_db` cho model routing, va khai bao day du `model_list`, `router_settings`, `fallbacks`, `context_window_fallbacks` trong `litellm_config.yaml`.
- Hoac neu van giu DB-backed models: phai co script idempotent duoc commit trong `deployment/server2/litellm/` de apply model rows + router settings vao `LiteLLM_Config` va `LiteLLM_ProxyModelTable`.

Manual edit tren dashboard/DB khong duoc xem la source of truth hop le.

### R2. Phai co `router_settings.fallbacks` khac `None`

It nhat cac model group chat sau phai duoc khai bao fallback:

- `editor8-gpt`
- `llmproxy`
- `copilot-mini-chat`
- `copilot-chat-mini`

Neu co consumer embedding production, bo sung:

- `text-embedding-3-small`

### R3. Fallback phai doc lap failure domain

Mot fallback hop le khong duoc cung luc:

- cung `api_base`
- cung container trung gian
- cung provider-account/quota pool

voi primary.

Noi cach khac:

- `editor8-gpt -> copilot-chat-mini` khong du de goi la fallback doc lap, vi ca hai deu dua vao `github-copilot-svcs:7071`.
- It nhat mot fallback cua moi model group chat production phai bypass hoan toan `github-copilot-svcs`.

### R4. Fallback chain toi thieu cho nhom chat production

Tai lieu nay yeu cau chain toi thieu nhu sau:

| Primary alias | Fallback 1 bat buoc | Fallback 2 bat buoc | Ghi chu |
|---|---|---|---|
| `editor8-gpt` | `gemini/gemini-3-flash-preview` | mot alias backup production moi, vi du `openai-gpt-5-backup` | Khong duoc dung alias ten `test-*` trong production |
| `llmproxy` | `gemini/gemini-3-flash-preview` | `openai-gpt-5-backup` | Tranh hard-fail khi GitHub/Copilot free pool can quota |
| `copilot-mini-chat` | `gemini/gemini-3-flash-preview` | `openai-gpt-5-backup` | Internal rotation khong du |
| `copilot-chat-mini` | `gemini/gemini-3-flash-preview` | `openai-gpt-5-backup` | Hien moi co 1 deployment |

Neu team khong muon dung ten `openai-gpt-5-backup`, co the doi ten alias. Requirement khong buoc dung ten nay, nhung buoc alias backup:

- la production-grade
- khong mang ten test
- doc lap voi `github-copilot-svcs`
- co kha nang chat/reasoning/tool-call phu hop nhu primary can

### R5. Fallback phai giu dung capability tier

Voi cac luong goi tu `editor8`, fallback phai giu duoc capability tier sau:

- chat
- reasoning
- function calling
- vision neu request co anh

Do do:

- `gemini/gemini-3-flash-preview` la fallback phu hop cho `editor8-gpt`
- mot alias chi ho tro chat co ban khong duoc dung lam fallback cuoi cho `editor8-gpt`

### R6. Mo rong capacity cho fallback Gemini

Hien tai env tren server co nhieu Google AI keys, nhung runtime chi dang dung `G1` va `G2`.

Requirement:

- Fallback Gemini production phai co it nhat 3 deployment/credential doc lap, hoac
- Neu team chu dong giu 2 deployment, phai co ly do ro rang va bo sung paid backup alias ngay sau Gemini trong chain

Khong duoc de fallback toan he thong phu thuoc vao 1-2 key ma khong co backup tiep theo.

### R7. Failure classes phai kick hoat fallback

Fallback phai duoc trigger cho toi thieu cac nhom loi sau:

- `429`
- `500`
- `502`
- `503`
- `504`
- connection refused
- DNS/connect timeout
- read timeout
- upstream auth/token transient failure
- provider unavailable / no healthy deployment

Neu can phan biet loi khong nen fallback, phai document ro. Mac dinh khong duoc "retry cung mot cho" den het request budget roi moi fail.

### R8. Timeout va retry phai duoc tai lieu hoa ro don vi

`LiteLLM_Config.router_settings.timeout` hien la `6000`, nhung tai lieu runtime khong giai thich ro day la ms hay s.

Requirement:

- Cau hinh moi phai ghi ro don vi timeout
- Per-attempt timeout phai duoc dat de fallback xay ra som
- Tong thoi gian cho mot request chat sau khi failover khong duoc vuot budget SLA ma team chap nhan

Moc de xuat:

- failover path cho chat request khong vuot `30s` tong cong trong tinh huong primary fail nhanh

### R9. Giu `drop_params: true`

Khong duoc bo `litellm_settings.drop_params: true`, vi cac fallback model hien khong dong nhat 100% ve supported OpenAI params.

Neu tat `drop_params`, nguy co request fail vi param mismatch se tang len khi chuyen model.

### R10. Embedding fallback neu co consumer production

Neu co luong production dung embedding, phai co fallback:

- `text-embedding-3-small -> text-embedding-ada-002`

Neu hien tai chua co consumer embedding production, muc nay co the de sau, nhung phai duoc danh dau ro la deferred, khong bo quen.

### R11. Quan sat va van hanh

Sau khi cau hinh xong, he thong phai cho phep quan sat:

- primary model group da duoc chon
- fallback nao da duoc thu
- fallback nao da thang
- request fail vi ly do gi sau khi da thu het chain

It nhat log phai du de tra loi cau hoi:

- request nay fail o upstream nao?
- da thu bao nhieu fallback?
- co fallback nao cung failure domain voi primary hay khong?

### R12. Khong duoc dua production vao alias ten test

`test-model` hien ton tai trong runtime, nhung khong duoc xem la ten alias production hop le trong tai lieu cau hinh moi.

Neu team muon tan dung deployment nay, phai:

- doi ten alias sang ten production ro nghia
- document provider/backing credential
- dua no vao chain fallback chinh danh

## 5. Sanitize target config mong muon

Snippet duoi day chi la huong cau hinh muc tieu, khong chua secret:

```yaml
general_settings:
  database_url: os.environ/DATABASE_URL
  master_key: os.environ/LITELLM_MASTER_KEY
  ui: false
  store_model_in_db: false # recommended for model routing source-of-truth

litellm_settings:
  drop_params: true

router_settings:
  routing_strategy: usage-based-routing
  num_retries: 1
  allowed_fails: 1
  cooldown_time: 30
  timeout: 30
  fallbacks:
    - {"editor8-gpt": ["gemini/gemini-3-flash-preview", "openai-gpt-5-backup"]}
    - {"llmproxy": ["gemini/gemini-3-flash-preview", "openai-gpt-5-backup"]}
    - {"copilot-mini-chat": ["gemini/gemini-3-flash-preview", "openai-gpt-5-backup"]}
    - {"copilot-chat-mini": ["gemini/gemini-3-flash-preview", "openai-gpt-5-backup"]}
    - {"text-embedding-3-small": ["text-embedding-ada-002"]}
```

Neu team van giu `store_model_in_db=true`, structure tren van phai ton tai trong DB, khong duoc chi nam o file.

## 6. Acceptance criteria

Mot implementation chi duoc xem la dat requirement neu qua du cac bai kiem tra sau:

1. Khi `github-copilot-svcs` tra `429`, request vao `editor8-gpt` van tra `200` tu fallback doc lap.
2. Khi `github-copilot-svcs` bi stop hoac khong connect duoc, request vao `llmproxy` van tra `200` tu fallback doc lap.
3. Khi 1 credential Gemini bi disable, request van pass qua credential Gemini con lai hoac paid backup alias.
4. Sau khi restart container `litellm`, config fallback van con hieu luc; khong mat do drift file/DB.
5. Runtime verification cho thay `fallbacks` khong con la `None`.
6. Khong con alias production nao phai dua vao `test-model`.
7. README/runbook trong `deployment/server2/litellm/` mo ta dung source of truth va cach reload/apply config.

## 7. Ket luan

Hien tai van de chinh khong phai "thieu them model", ma la "thieu fallback doc lap va thieu source-of-truth ro rang".

Neu chi them replica trong cung `github-copilot-svcs` thi he thong van co the fail cung khi quota/free-model pool can het.

De dat muc tieu "he thong khong failed", server2 can:

- fallback chain co that su doc lap
- source-of-truth model routing nam ro trong `deployment/server2/litellm/`
- verification bang fault-injection/stop-service, khong chi bang request happy-path
