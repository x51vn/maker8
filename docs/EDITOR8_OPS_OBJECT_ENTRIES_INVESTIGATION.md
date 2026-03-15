# Editor8 `/ops` Investigation: `Cannot convert undefined or null to object`

## Summary

Khi truy cập `editor8-frontend/ops`, browser ném lỗi:

```text
Uncaught TypeError: Cannot convert undefined or null to object
    at Object.entries (<anonymous>)
```

Kết luận điều tra: nguyên nhân chính là contract mismatch giữa frontend và backend cho endpoint `GET /api/health/detailed`.

Frontend render `Object.entries(health.services)`, nhưng backend lại trả object theo key `checks`, không phải `services`. Vì vậy `health.services` là `undefined`, và `Object.entries(undefined)` gây crash ngay trên client.

## Scope investigated

- route frontend: `../editor8/frontend/src/app/ops/page.tsx`
- components hiển thị trang ops:
  - `../editor8/frontend/src/components/ops/HealthStatusCard.tsx`
  - `../editor8/frontend/src/components/ops/MetricsSummary.tsx`
  - `../editor8/frontend/src/components/ops/FailureList.tsx`
- frontend types:
  - `../editor8/frontend/src/types/index.ts`
- backend endpoints/data producers:
  - `../editor8/backend/src/editor8/health.py`
  - `../editor8/backend/src/editor8/api/routes.py`
  - `../editor8/backend/src/editor8/api/monitoring.py`
  - `../editor8/backend/src/editor8/services/metrics.py`

## Investigation process

### 1. Tìm tất cả chỗ dùng `Object.entries` trong frontend

Search trong `../editor8/frontend/src` cho thấy trên route `/ops` chỉ có hai chỗ đáng nghi:

- `../editor8/frontend/src/components/ops/HealthStatusCard.tsx:49`
  `Object.entries(health.services)`
- `../editor8/frontend/src/components/ops/MetricsSummary.tsx:97`
  `Object.entries(jobs.by_status)`

Điều này thu hẹp lỗi xuống 2 component trên trang `/ops`.

### 2. Truy data flow của trang `/ops`

`../editor8/frontend/src/app/ops/page.tsx` fetch song song 3 nguồn dữ liệu:

- `api.getDetailedHealth()` -> `/api/health/detailed`
- `api.getMetrics(24)` -> `/api/metrics?hours=24`
- `api.listJobs({ status: 'FAILED', page_size: 10 })` -> `/api/jobs?...`

Sau đó page truyền:

- `health` vào `HealthStatusCard`
- `metrics` vào `MetricsSummary`
- `jobs?.items ?? []` vào `FailureList`

`FailureList` không dùng `Object.entries`, nên không phải nguồn gây lỗi này.

### 3. Đối chiếu `HealthStatusCard` với payload backend thật

Frontend type khai báo:

- `../editor8/frontend/src/types/index.ts:337`

```ts
export interface DetailedHealth {
  status: HealthStatus;
  services: Record<string, ServiceHealth>;
}
```

Frontend component dùng:

- `../editor8/frontend/src/components/ops/HealthStatusCard.tsx:49`

```tsx
Object.entries(health.services).map(...)
```

Nhưng backend lại trả:

- `../editor8/backend/src/editor8/health.py:39`

```py
return {
    "status": "ok" if all_ok else "degraded",
    "checks": {
        "database": db,
        "kafka_producer": kafka,
    },
}
```

Payload backend thực tế có shape:

```json
{
  "status": "ok|degraded",
  "checks": {
    "database": { "...": "..." },
    "kafka_producer": { "...": "..." }
  }
}
```

Trong khi frontend đang mong:

```json
{
  "status": "ok|degraded|error",
  "services": {
    "database": { "...": "..." },
    "kafka_producer": { "...": "..." }
  }
}
```

Đây là mismatch trực tiếp và đủ để giải thích lỗi:

1. `/api/health/detailed` trả `200 OK`
2. `OpsPage` set state `health` bằng JSON nhận được
3. `HealthStatusCard` render với `health !== null`
4. `health.services` là `undefined` vì backend trả `checks`
5. `Object.entries(undefined)` ném `TypeError`

## Why this is the most likely exact crash path

`MetricsSummary` cũng dùng `Object.entries`, nhưng current backend code cho `/api/metrics` có trả `jobs.by_status`:

- `../editor8/backend/src/editor8/services/metrics.py:39`

```py
by_status = {row.status: row.cnt for row in status_result}
```

và response `/api/metrics` bọc lại object đó vào `jobs.by_status`.

Ngược lại, với health endpoint thì mismatch là chắc chắn và hiện hữu ngay trong code hiện tại. Vì vậy `HealthStatusCard` là nguyên nhân có xác suất cao nhất, và thực tế là đủ để gây crash độc lập.

## Secondary contract mismatches found

Trong lúc đối chiếu, có thêm các drift khác trên cùng trang `/ops`:

### A. Service status enum không khớp

Frontend type:

- `../editor8/frontend/src/types/index.ts:331`

```ts
export interface ServiceHealth {
  status: HealthStatus;
}
```

với:

```ts
export type HealthStatus = 'ok' | 'degraded' | 'error';
```

Nhưng backend đang trả các status con như:

- `../editor8/backend/src/editor8/health.py:23`
  `fail`
- `../editor8/backend/src/editor8/health.py:30`
  `disconnected`

Chỗ này không gây `Object.entries` crash, nhưng vẫn là contract drift. Hiện component rơi vào fallback `STATUS_CONFIG.error`.

### B. `AgentMetrics.by_type` không khớp key field

Frontend type:

- `../editor8/frontend/src/types/index.ts:363`

```ts
by_type: Record<string, { calls: number; avg_ms: number }>;
```

Backend metrics lại trả:

- `../editor8/backend/src/editor8/services/metrics.py:102`

```py
{
    "calls": row.calls,
    "avg_latency_ms": round(float(row.avg_ms or 0), 1),
}
```

Tức là frontend chờ `avg_ms`, backend trả `avg_latency_ms`.

Hiện tại `/ops` chưa dùng `agents.by_type`, nên mismatch này chưa tạo ra crash đang thấy. Nhưng đây là một bug contract khác cùng họ.

## Why there are no server logs

Điều này phù hợp với bug client-side:

- backend không cần throw exception nếu `/api/health/detailed` vẫn trả JSON hợp lệ về mặt HTTP
- frontend server cũng có thể không log gì nếu page render phía client rồi crash trong browser
- lỗi phát sinh sau khi dữ liệu đã tới client và React render `Object.entries(undefined)`

Nói ngắn gọn: đây là lỗi contract/runtime ở browser, không phải lỗi backend 500.

## Additional observation

Hash bundle trong browser stack trace:

```text
page-6dba62f1385b57f4.js
```

không trùng với bundle hash hiện có trong checkout local:

```text
../editor8/frontend/.next/static/chunks/app/ops/page-17462b2a682b63a5.js
```

Điều này cho thấy build deploy đang chạy không hoàn toàn trùng hash với local build hiện tại. Tuy nhiên nó không làm thay đổi kết luận, vì source hiện tại vẫn chứa đúng các call site và contract mismatch nêu trên.

## Testing gap found

Search trong `../editor8/frontend/src/__tests__` không thấy test nào bao phủ route `/ops`, `HealthStatusCard`, hoặc integration giữa `/api/health/detailed` và type `DetailedHealth`.

Hệ quả:

- mismatch `checks` vs `services` lọt qua compile-time
- mismatch `avg_ms` vs `avg_latency_ms` cũng lọt qua compile-time

## Root cause

Root cause chính:

- frontend `/ops` kỳ vọng payload health có key `services`
- backend `/api/health/detailed` trả key `checks`
- component `HealthStatusCard` gọi `Object.entries(health.services)` mà không guard

## Recommended fix direction

Chưa áp dụng fix trong turn này, nhưng hướng sửa đúng là:

1. Đồng bộ contract health giữa backend và frontend.
   Chọn một:
   - backend đổi `checks` -> `services`
   - hoặc frontend đổi sang đọc `health.checks`

2. Đồng bộ enum status cho service health.
   Chọn một:
   - backend map `fail` / `disconnected` về `error` / `degraded`
   - hoặc frontend mở rộng union type và UI mapping cho các status này

3. Đồng bộ luôn contract metrics `agents.by_type`.
   Chọn một:
   - backend trả `avg_ms`
   - hoặc frontend đổi type sang `avg_latency_ms`

4. Thêm test cho `/ops`:
   - test render với payload `/api/health/detailed`
   - test render với payload `/api/metrics`
   - test contract mismatch không được làm page crash

## Confidence

High confidence.

Mấu chốt là mismatch `health.services` vs backend `checks` là trực tiếp, rõ ràng, và tự nó đã đủ để sinh ra đúng lỗi `Cannot convert undefined or null to object` ở line `Object.entries(...)`.
